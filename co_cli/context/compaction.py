"""Public compaction entry points: summarization, overflow recovery, pre-turn hygiene.

Imported outside ``co_cli/context/`` (agent/, commands/, prompts/, orchestrate)
so this module is package-public. Implementation details — turn grouping,
boundary planning, marker builders, enrichment gathering, and history
processors — live in package-private siblings and are re-exported here so
external callers have a single import surface.

Submodule map:
    _compaction_boundaries  — TurnGroup, group_by_turn, plan_compaction_boundaries
    _compaction_markers     — static/summary/todo markers, enrichment context
    history_processors     — dedup_tool_results, evict_old_tool_results
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)

from co_cli.config.core import DREAM_SNAPSHOTS_DIR
from co_cli.context._compaction_boundaries import (
    CompactionBoundaries,
    TurnGroup,
    find_first_run_end,
    group_by_turn,
    groups_to_messages,
    plan_compaction_boundaries,
)
from co_cli.context._compaction_markers import (
    STATIC_MARKER_PREFIX,
    SUMMARY_MARKER_PREFIX,
    TODO_SNAPSHOT_PREFIX,
    build_compaction_marker,
    build_todo_snapshot,
    extract_summary_body,
    gather_compaction_context,
    is_compaction_marker,
    static_marker,
    summary_marker,
)
from co_cli.context._http_error_classifier import is_context_overflow
from co_cli.context._tool_result_markers import is_cleared_marker
from co_cli.context.history_processors import strip_all_tool_returns
from co_cli.context.summarization import (
    effective_request_tokens,
    estimate_message_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.deps import CoDeps
from co_cli.observability.tracing import current_span, trace
from co_cli.session.persistence import append_messages
from co_cli.session.review_kick import write_review_kick

__all__ = [
    "STATIC_MARKER_PREFIX",
    "SUMMARY_MARKER_PREFIX",
    "TODO_SNAPSHOT_PREFIX",
    "CompactionBoundaries",
    "TurnGroup",
    "build_compaction_marker",
    "build_todo_snapshot",
    "commit_compaction",
    "compact_messages",
    "estimate_message_tokens",
    "find_first_run_end",
    "gather_compaction_context",
    "group_by_turn",
    "groups_to_messages",
    "is_cleared_marker",
    "is_compaction_marker",
    "is_context_overflow",
    "plan_compaction_boundaries",
    "proactive_window_processor",
    "recover_overflow_history",
    "resolve_compaction_budget",
    "static_marker",
    "summarize_dropped_messages",
    "summarize_messages",
    "summary_marker",
]

log = logging.getLogger(__name__)


def _reset_thrash_state(ctx: RunContext[CoDeps]) -> None:
    """Reset proactive-compaction thrash counters after a forced overflow recovery.

    Recovery paths reset these unconditionally — overflow already proves the
    system needed to compact, so crediting it as a clean resync prevents the
    gate from staying tripped and suppressing the next proactive run. The hint
    re-arms with the counter per the banner-text contract. Not called from the
    proactive path: it must NOT reset on every successful compaction (the
    thrash gate is there to suppress repeated low-yield runs).
    """
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0


_COMPACTION_BREAKER_TRIP: int = 3
"""Consecutive summarization failures that trip the circuit breaker."""

_COMPACTION_BREAKER_PROBE_EVERY: int = 10
"""Once tripped, allow one LLM probe attempt every N blocked calls.
First probe fires at skip_count == TRIP + PROBE_EVERY (i.e. 13), then every
PROBE_EVERY counts thereafter (23, 33, …). A successful probe resets the counter.
"""


class CompactionFallbackReason(StrEnum):
    """Why a compaction pass degraded to a static marker instead of an LLM summary.

    Emitted as the ``reason`` on the ``compaction_fallback`` span event so a silent
    degradation (the 71.95s-scare failure mode) is attributable in ``co trace``
    rather than surfacing only as a log line. Each cause is distinct — a single
    opaque reason would make all four look identical at triage.
    """

    MODEL_ABSENT = "model_absent"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    SUMMARIZER_ERROR = "summarizer_error"
    EMPTY_SUMMARY = "empty_summary"


def _emit_compaction_fallback(reason: CompactionFallbackReason) -> None:
    """Attach a ``compaction_fallback`` event (with reason) to the active span.

    No-op when no span is active (``current_span()`` returns the documented no-op),
    so callers outside an active trace are safe.
    """
    current_span().add_event("compaction_fallback", {"reason": reason.value})


def _summarization_gate_open(ctx: RunContext[CoDeps]) -> tuple[bool, bool]:
    """Decide whether the LLM summarizer may run for the next compaction pass.

    Read-only; never mutates runtime. Returns ``(gate_open, is_probe)``.
    ``gate_open`` is False when the model is absent or the circuit breaker
    requires a skip. ``is_probe`` is True when the breaker allows a probe
    attempt at the current skip count. The caller owns all writes to
    ``compaction_skip_count``.
    """
    if not ctx.deps.model:
        log.info("Compaction: model absent, using static marker")
        _emit_compaction_fallback(CompactionFallbackReason.MODEL_ABSENT)
        return (False, False)

    count = ctx.deps.runtime.compaction_skip_count
    # skips_since_trip == 0 blocks the initial trip; probes fire every PROBE_EVERY skips after that
    skips_since_trip = count - _COMPACTION_BREAKER_TRIP
    if count < _COMPACTION_BREAKER_TRIP:
        return (True, False)
    if skips_since_trip != 0 and skips_since_trip % _COMPACTION_BREAKER_PROBE_EVERY == 0:
        return (True, True)
    log.warning("Compaction: circuit breaker active (count=%d), static marker", count)
    _emit_compaction_fallback(CompactionFallbackReason.CIRCUIT_BREAKER_OPEN)
    return (False, False)


def _marker_content(msg: ModelMessage) -> str | None:
    """Return the marker content string if ``msg`` is a compaction marker, else None.

    Markers are single-part ``ModelRequest``s whose ``UserPromptPart`` content
    starts with a compaction sentinel; the content is needed to recover the recap.
    """
    if not isinstance(msg, ModelRequest):
        return None
    for part in msg.parts:
        if isinstance(part, UserPromptPart) and is_compaction_marker(part.content):
            return part.content
    return None


def _partition_dropped(
    dropped: list[ModelMessage],
) -> tuple[list[ModelMessage], str | None]:
    """Split ``dropped`` into (marker-free body, latest prior-summary recap).

    Compaction markers (summary or static) are stripped from the body so the
    summarizer never sees a prior marker inline inside the opaque turns block.
    The latest summary marker's recap is recovered via ``extract_summary_body``
    and returned as the ``prior_summary`` anchor; static markers carry no recap
    and contribute None. The todo snapshot is NOT a compaction marker, so it
    stays in the body (regenerated fresh each pass — unchanged from today).
    """
    body: list[ModelMessage] = []
    prior_summary: str | None = None
    for msg in dropped:
        content = _marker_content(msg)
        if content is None:
            body.append(msg)
            continue
        recap = extract_summary_body(content)
        if recap is not None:
            prior_summary = recap
    return body, prior_summary


async def summarize_dropped_messages(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
    *,
    focus: str | None = None,
    prior_summary: str | None = None,
) -> str:
    """Pure summarizer call over ``dropped`` — no gate, no fallback.

    Callers must call ``_summarization_gate_open(ctx)`` first; this function assumes
    a model is configured and the circuit breaker permits the LLM call. Raises on
    summarizer failure (including ``asyncio.CancelledError``). ``prior_summary``,
    when present, is the recovered recap of a previous compaction pass; it is fed
    to the summarizer in a dedicated slot rather than inline in the turns block.
    """
    enrichment = gather_compaction_context(ctx)
    return await summarize_messages(
        ctx.deps,
        dropped,
        personality_active=bool(ctx.deps.config.personality),
        context=enrichment,
        focus=focus,
        prior_summary=prior_summary,
    )


def _is_valid_summary(text: str | None) -> bool:
    """Rejects empty/whitespace-only output; accepts any non-empty string."""
    return bool(text and text.strip())


async def _gated_summarize_or_none(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
    *,
    focus: str | None,
    prior_summary: str | None = None,
) -> str | None:
    """Run the summarizer if the gate is open, else return None.

    Fires the user-visible "Compacting conversation..." status callback once
    the gate is confirmed open (so static-marker / model-absent paths stay
    silent). Resets ``compaction_skip_count`` on a valid (non-empty) summary,
    and falls through to a None return when the summarizer raises or returns
    empty. Lets ``asyncio.CancelledError`` propagate.
    """
    gate_open, is_probe = _summarization_gate_open(ctx)
    if not gate_open:
        ctx.deps.runtime.compaction_skip_count += 1
        return None
    if is_probe:
        log.info(
            "Compaction: circuit breaker probe (count=%d)", ctx.deps.runtime.compaction_skip_count
        )

    if (cb := ctx.deps.runtime.status_callback) is not None:
        cb("Compacting conversation...")

    try:
        summary_text = await summarize_dropped_messages(
            ctx, dropped, focus=focus, prior_summary=prior_summary
        )
    except Exception:
        log.warning(
            "Compaction summarization failed — falling back to static marker", exc_info=True
        )
        _emit_compaction_fallback(CompactionFallbackReason.SUMMARIZER_ERROR)
        ctx.deps.runtime.compaction_skip_count += 1
        return None
    if not _is_valid_summary(summary_text):
        log.warning(
            "Compaction summarizer returned empty output — counting as failure (count=%d)",
            ctx.deps.runtime.compaction_skip_count + 1,
        )
        _emit_compaction_fallback(CompactionFallbackReason.EMPTY_SUMMARY)
        ctx.deps.runtime.compaction_skip_count += 1
        return None
    ctx.deps.runtime.compaction_skip_count = 0
    return summary_text


def _snapshot_and_kick_review(ctx: RunContext[CoDeps], messages: list[ModelMessage]) -> None:
    """Snapshot the pre-compaction message list and fire a memory review KICK.

    Defect-B fix: compaction rewrites the live transcript in place, lossily, so a
    review that runs afterward reads co's own summary marker instead of the
    original turns. Capturing here — at the one chokepoint where whole messages
    are dropped — preserves the pre-drop content at full fidelity in an immutable
    snapshot and points a memory review KICK at it (the daemon reads it uncapped).

    Once per logical compaction: ``proactive_window_processor`` sets the one-shot
    ``skip_compaction_snapshot`` flag only around its no-progress escalation call,
    which re-enters ``compact_messages`` for the SAME logical compaction — so that
    re-entry is suppressed while a genuinely separate later-in-turn compaction
    (a fresh processor pass) still snapshots. Best-effort — any failure is logged
    and never aborts the compaction it rides on.
    """
    deps = ctx.deps
    if deps.runtime.skip_compaction_snapshot:
        return
    if not messages or not deps.config.skills.review_enabled or deps.model is None:
        return
    session_id = deps.session.session_path.stem if deps.session.session_path else ""
    if not session_id:
        return
    try:
        DREAM_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S.%f")
        snapshot_path = DREAM_SNAPSHOTS_DIR / f"{session_id}-{ts}-{uuid4()}.jsonl"
        append_messages(snapshot_path, messages)
        write_review_kick(
            domain="memory",
            session_id=session_id,
            persisted_message_count=None,
            transcript_override=str(snapshot_path),
        )
    except Exception:
        log.warning("compaction review snapshot/kick failed", exc_info=True)


async def compact_messages(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    bounds: CompactionBoundaries,
    *,
    focus: str | None = None,
    summarize: bool = True,
) -> tuple[list[ModelMessage], str | None]:
    """Compact ``messages[head_end:tail_start]`` into a marker; assemble result.

    Slices by ``bounds`` and assembles ``head | marker | [todo_snapshot] |
    tail``. The dropped middle is partitioned (``_partition_dropped``) into a
    marker-free body and the latest prior-summary recap. When ``summarize`` is
    True (default), runs the gated summarizer over that body — never over prior
    compaction markers — with the recovered recap fed through a dedicated
    ``prior_summary`` slot; when ``summarize`` is False, the summarizer is skipped
    entirely
    and ``summary_text`` stays None, driving ``build_compaction_marker`` down
    its static-marker branch (no LLM call). Returns ``(result, summary_text)``;
    ``summary_text`` is None when the summarizer fell back to a static marker
    *or* when ``summarize=False`` forced the static path.

    Does NOT write runtime — the caller commits via ``commit_compaction`` as
    its last step (Task-3 invariant: any exception before commit leaves
    runtime untouched). It does best-effort-snapshot the pre-drop ``messages``
    for a memory review KICK (Defect-B; see ``_snapshot_and_kick_review``) — a
    fire-and-forget filesystem write, not a runtime mutation.
    """
    _snapshot_and_kick_review(ctx, messages)
    head_end, tail_start, _ = bounds
    head = messages[:head_end]
    dropped = messages[head_end:tail_start]
    tail = messages[tail_start:]
    body, prior_summary = _partition_dropped(dropped)
    summary_text = (
        await _gated_summarize_or_none(ctx, body, focus=focus, prior_summary=prior_summary)
        if summarize
        else None
    )
    has_tail = len(tail) > 0
    marker = build_compaction_marker(len(dropped), summary_text, has_tail=has_tail)
    todo_snapshot = build_todo_snapshot(ctx.deps.session.session_todos)
    result = [
        *head,
        marker,
        *([todo_snapshot] if todo_snapshot is not None else []),
        *tail,
    ]
    return result, summary_text


def commit_compaction(
    ctx: RunContext[CoDeps],
    result: list[ModelMessage],
) -> None:
    """Mark that compaction ran this turn.

    Single writer of ``compaction_applied_this_turn`` — drives session-branching
    (main.py) and the ``proactive_window_processor`` OTEL span attribute.

    Callers must invoke this as the last step before returning.
    """
    ctx.deps.runtime.compaction_applied_this_turn = True


def _record_proactive_outcome(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    result: list[ModelMessage],
    summary_text: str | None,
    *,
    token_count: int,
    summary_skipped: bool = False,
) -> int:
    """Apply proactive-specific post-compaction policy; return the post-pass token count.

    Fires the closing status callback, computes savings using ``token_count``
    (the trigger's realtime-local ``effective_request_tokens``), emits execution OTEL attributes
    onto the wrapper span, updates the anti-thrash counter, writes the
    post-compaction estimate back to runtime so the status line reflects the
    compacted size, then commits runtime as the final step. Returns
    ``tokens_after`` (the post-compaction ``effective_request_tokens``) so the
    caller can detect a no-progress pass without recomputing. Any exception
    before ``commit_compaction`` leaves runtime untouched — single-writer
    atomicity for the "applied" fields.

    The closing status callback has four cases. ``summary_skipped`` is True
    only on the anti-thrash static fallback, where the summarizer was *never
    run by design* — so it must NOT report "Summarizer failed". A successful
    summary reports "Compacted."; a missing model reports unavailability; a
    genuine summarizer failure (model present, summary came back None, not a
    deliberate skip) keeps the "Summarizer failed" wording.
    """
    cfg = ctx.deps.config.compaction

    if (cb := ctx.deps.runtime.status_callback) is not None:
        if summary_text is not None:
            cb("Compacted.")
        elif summary_skipped:
            cb("Compacted (static marker).")
        elif ctx.deps.model is None:
            cb("LLM compaction unavailable — used static marker.")
        else:
            cb("Summarizer failed — used static marker.")

    tokens_after = effective_request_tokens(ctx.deps, result)
    savings = (token_count - tokens_after) / token_count if token_count > 0 else 0.0
    log.debug(
        "Compaction result: tokens %d→%d (saved %.0f%%) msgs %d→%d",
        token_count,
        tokens_after,
        savings * 100,
        len(messages),
        len(result),
    )

    span = current_span()
    span.set_attribute("compaction.tokens_after", tokens_after)
    span.set_attribute("compaction.savings_pct", round(savings * 100, 1))
    span.set_attribute("compaction.msgs_after", len(result))
    span.set_attribute("compaction.fired", True)

    if savings < cfg.min_proactive_savings:
        ctx.deps.runtime.consecutive_low_yield_proactive_compactions += 1
    else:
        ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0

    ctx.deps.runtime.current_request_tokens_estimate = tokens_after
    commit_compaction(ctx, result)
    return tokens_after


async def recover_overflow_history(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage] | None:
    """Single-tier overflow recovery: strip-then-summarize.

    PATH 1: strip every ToolReturnPart to a per-tool semantic marker (no
            recency cap, no boundary). If the stripped history fits the
            budget, return it directly — no LLM call.

    PATH 2: run ``plan_compaction_boundaries`` + ``compact_messages`` on the
            stripped history. Returns None when the planner cannot find valid
            bounds (e.g. only one turn group total) — caller (run_turn) drives
            the terminal error path.

    Replaces the previous two-tier cascade. The strip primitive is more
    granular than the structural emergency tier was: it preserves message
    count and replaces tool returns with named per-tool markers (intent +
    outcome) instead of dropping all middle groups to a single static marker.
    """
    if not messages:
        return None

    stripped = strip_all_tool_returns(messages)
    stripped_tokens = estimate_message_tokens(stripped)

    budget = resolve_compaction_budget(ctx.deps)
    if stripped_tokens <= budget:
        # PATH 1: strip-only-fits — no LLM, no marker, just commit and return.
        commit_compaction(ctx, stripped)
        _reset_thrash_state(ctx)
        return stripped

    cfg = ctx.deps.config.compaction
    bounds = plan_compaction_boundaries(
        stripped,
        budget,
        cfg.tail_fraction,
        static_floor_tokens=ctx.deps.static_floor_tokens,
        compaction_ratio=cfg.compaction_ratio,
    )
    if bounds is None:
        log.warning(
            "Compaction: overflow recovery boundary planning returned None after strip. "
            "budget=%d tail_fraction=%.2f token_count=%d — terminal.",
            budget,
            cfg.tail_fraction,
            stripped_tokens,
        )
        return None

    # PATH 2: summarize, assemble, commit.
    result, _ = await compact_messages(ctx, stripped, bounds, focus=None)
    commit_compaction(ctx, result)
    _reset_thrash_state(ctx)
    return result


def _resolve_proactive_focus(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> str | None:
    for todo in ctx.deps.session.session_todos:
        if todo["status"] == "in_progress":
            return todo["content"][:200]
    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if not isinstance(part, UserPromptPart):
                    continue
                content = part.content
                if not isinstance(content, str):
                    continue
                # Skip inserted compaction markers (summary/static) and the todo
                # snapshot — focus must anchor on a real user message, never on
                # marker boilerplate. is_compaction_marker does not match the todo
                # snapshot, so TODO_SNAPSHOT_PREFIX is tested explicitly.
                if is_compaction_marker(content) or content.startswith(TODO_SNAPSHOT_PREFIX):
                    continue
                return content[-200:]
    return None


@trace("compaction.proactive_check")
async def proactive_window_processor(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Mid-turn compaction trigger — the only auto-compaction layer.

    Registered as the last history processor on the orchestrator agent. Fires
    before each ModelRequestNode when token pressure exceeds compaction_ratio.
    Anti-thrash gate engages after ``proactive_thrash_window`` consecutive low-
    yield runs; once tripped, compaction degrades to a cheap static-marker pass
    (drop the region, insert a marker, no LLM summary) instead of paying for
    another low-yield summarization — it never stops trimming. "Whether to
    compact at all" is owned solely by the below-threshold check and the
    boundary-``None`` guard.

    Fail-open: any Exception returns ``messages`` unchanged so the agent loop
    proceeds. asyncio.CancelledError (via BaseException) propagates.

    Pre-turn lifecycle slot is intentionally compaction-free — see
    docs/specs/compaction.md for the design choice.
    """
    try:
        budget = resolve_compaction_budget(ctx.deps)
        if budget <= 0:
            return messages
        cfg = ctx.deps.config.compaction

        # Trigger is the realtime-local count (floor-inclusive: static prefill + message list),
        # no provider-reported floor — peer-aligned with hermes/openclaw. A successful L2 spill
        # deterministically lowers this same value, so L3 re-reads the lowered payload and
        # fast-paths when the spill already fit. Savings reuses the same effective-before.
        token_count = effective_request_tokens(ctx.deps, messages)
        token_threshold = int(budget * cfg.compaction_ratio)

        tool_calls_in_history = sum(
            1
            for m in messages
            if isinstance(m, ModelResponse)
            for p in m.parts
            if isinstance(p, ToolCallPart)
        )

        log.debug(
            "Proactive check: msgs=%d count=%d threshold=%d tool_calls=%d",
            len(messages),
            token_count,
            token_threshold,
            tool_calls_in_history,
        )

        span = current_span()
        span.set_attribute("compaction.msgs", len(messages))
        span.set_attribute("compaction.local_tokens", token_count)
        span.set_attribute("compaction.token_count", token_count)
        span.set_attribute("compaction.threshold", token_threshold)
        span.set_attribute("compaction.budget", budget)
        span.set_attribute("compaction.tool_calls_in_history", tool_calls_in_history)
        span.set_attribute(
            "compaction.applied_this_turn", ctx.deps.runtime.compaction_applied_this_turn
        )

        from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_REQUEST

        span.set_attribute("compaction.tool_call_limit", MAX_TOOL_CALLS_PER_MODEL_REQUEST)
        span.set_attribute(
            "compaction.request_tokens_estimate",
            ctx.deps.runtime.current_request_tokens_estimate or -1,
        )

        if token_count <= token_threshold:
            span.set_attribute("compaction.fired", False)
            span.set_attribute("compaction.skip_reason", "below_threshold")
            return messages

        log.debug(
            "Compaction trigger: tokens=%d threshold=%d budget=%d msgs=%d",
            token_count,
            token_threshold,
            budget,
            len(messages),
        )

        # Anti-thrash gate: after proactive_thrash_window consecutive low-yield
        # summary passes, demote to a cheap static-marker compaction instead of
        # paying for another LLM summary. The gate flips summarize off — it
        # never short-circuits the trim. The shared boundary→compact→record tail
        # below runs for both paths so the None-boundary guard, token_count, and
        # span writes stay uniform; only the summarize flag (and the resulting
        # marker content) differs.
        summarize = True
        if (
            ctx.deps.runtime.consecutive_low_yield_proactive_compactions
            >= cfg.proactive_thrash_window
        ):
            log.info("Compaction: anti-thrashing gate active, using static marker")
            span.set_attribute("compaction.skip_reason", "anti_thrash_gate")
            summarize = False

        bounds = plan_compaction_boundaries(
            messages,
            budget,
            cfg.tail_fraction,
            static_floor_tokens=ctx.deps.static_floor_tokens,
            compaction_ratio=cfg.compaction_ratio,
        )
        if bounds is None:
            log.warning(
                "Compaction: boundary planning returned None. "
                "budget=%d tail_fraction=%.2f — no compaction possible.",
                budget,
                cfg.tail_fraction,
            )
            span.set_attribute("compaction.fired", False)
            span.set_attribute("compaction.skip_reason", "no_boundary")
            return messages

        head_end, tail_start, dropped_count = bounds
        log.debug(
            "Compaction boundaries: msgs=%d head=0..%d dropped=%d..%d(%d) tail=%d..%d(%d)",
            len(messages),
            head_end,
            head_end,
            tail_start,
            dropped_count,
            tail_start,
            len(messages),
            len(messages) - tail_start,
        )

        focus = _resolve_proactive_focus(ctx, messages)
        result, summary_text = await compact_messages(
            ctx, messages, bounds, focus=focus, summarize=summarize
        )
        if summary_text is not None:
            log.info("Sliding window: summarised %d messages inline", dropped_count)

        tokens_after = _record_proactive_outcome(
            ctx,
            messages,
            result,
            summary_text,
            token_count=token_count,
            summary_skipped=not summarize,
        )
        # No-progress guard: if the applied pass did not shrink the payload, a
        # re-fired identical pass would thrash forever. Escalate once to overflow
        # recovery (strip-then-summarize). _record_proactive_outcome already
        # committed + bumped the thrash counter; recover_overflow_history
        # re-commits (idempotent) and resets the thrash state — the desired
        # post-recovery state. Fail-open: a None recovery returns messages.
        if tokens_after >= token_count:
            log.error(
                "Compaction made no progress: tokens %d→%d — escalating to overflow recovery",
                token_count,
                tokens_after,
            )
            span.set_attribute("compaction.no_progress_escalation", True)
            # Suppress the snapshot for the escalation's re-entry into compact_messages:
            # it is the SAME logical compaction the proactive pass already snapshotted.
            ctx.deps.runtime.skip_compaction_snapshot = True
            try:
                recovered = await recover_overflow_history(ctx, messages)
            finally:
                ctx.deps.runtime.skip_compaction_snapshot = False
            return recovered if recovered is not None else messages
        return result
    except Exception:
        log.warning("Mid-turn compaction failed — skipping", exc_info=True)
        return messages
