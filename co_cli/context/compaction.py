"""Public compaction entry points: summarization, overflow recovery, pre-turn hygiene.

Imported outside ``co_cli/context/`` (agent/, commands/, prompts/, orchestrate)
so this module is package-public. Implementation details — turn grouping,
boundary planning, marker builders, enrichment gathering, and history
processors — live in package-private siblings and are re-exported here so
external callers have a single import surface.

Submodule map:
    _compaction_boundaries  — TurnGroup, group_by_turn, plan_compaction_boundaries
    _compaction_markers     — static/summary/todo markers, enrichment context
    history_processors     — dedup_tool_results, evict_old_tool_results,
                             sanitize_surrogate_codepoints
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

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
    gather_compaction_context,
    is_compaction_marker,
    static_marker,
    summary_marker,
)
from co_cli.context._http_error_classifier import is_context_overflow
from co_cli.context.history_processors import strip_all_tool_returns
from co_cli.context.summarization import (
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.deps import CoDeps
from co_cli.observability.tracing import current_span, trace

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
    "is_compaction_marker",
    "is_context_overflow",
    "latest_response_input_tokens",
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
        return (False, False)

    count = ctx.deps.runtime.compaction_skip_count
    # skips_since_trip == 0 blocks the initial trip; probes fire every PROBE_EVERY skips after that
    skips_since_trip = count - _COMPACTION_BREAKER_TRIP
    if count < _COMPACTION_BREAKER_TRIP:
        return (True, False)
    if skips_since_trip != 0 and skips_since_trip % _COMPACTION_BREAKER_PROBE_EVERY == 0:
        return (True, True)
    log.warning("Compaction: circuit breaker active (count=%d), static marker", count)
    return (False, False)


async def summarize_dropped_messages(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
    *,
    focus: str | None = None,
) -> str:
    """Pure summarizer call over ``dropped`` — no gate, no fallback.

    Callers must call ``_summarization_gate_open(ctx)`` first; this function assumes
    a model is configured and the circuit breaker permits the LLM call. Raises on
    summarizer failure (including ``asyncio.CancelledError``).
    """
    enrichment = gather_compaction_context(ctx)
    return await summarize_messages(
        ctx.deps,
        dropped,
        personality_active=bool(ctx.deps.config.personality),
        context=enrichment,
        focus=focus,
    )


def _is_valid_summary(text: str | None) -> bool:
    """Rejects empty/whitespace-only output; accepts any non-empty string."""
    return bool(text and text.strip())


async def _gated_summarize_or_none(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
    *,
    focus: str | None,
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
        summary_text = await summarize_dropped_messages(ctx, dropped, focus=focus)
    except Exception:
        log.warning(
            "Compaction summarization failed — falling back to static marker", exc_info=True
        )
        ctx.deps.runtime.compaction_skip_count += 1
        return None
    if not _is_valid_summary(summary_text):
        log.warning(
            "Compaction summarizer returned empty output — counting as failure (count=%d)",
            ctx.deps.runtime.compaction_skip_count + 1,
        )
        ctx.deps.runtime.compaction_skip_count += 1
        return None
    ctx.deps.runtime.compaction_skip_count = 0
    return summary_text


def _preserve_search_tool_breadcrumbs(
    dropped: list[ModelMessage],
) -> list[ModelMessage]:
    """Keep paired search_tools tool-call/return cycles across compaction boundaries."""
    call_part_by_id: dict[str, ToolCallPart] = {}
    for msg in dropped:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == "search_tools":
                    call_part_by_id[part.tool_call_id] = part

    result: list[ModelMessage] = []
    for msg in dropped:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not (isinstance(part, ToolReturnPart) and part.tool_name == "search_tools"):
                continue
            call_part = call_part_by_id.get(part.tool_call_id)
            if call_part is None:
                # Orphan return without paired call — drop rather than emit broken shape.
                continue
            result.append(ModelResponse(parts=[call_part]))
            result.append(ModelRequest(parts=[part]))
    return result


async def compact_messages(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    bounds: CompactionBoundaries,
    *,
    focus: str | None = None,
) -> tuple[list[ModelMessage], str | None]:
    """Compact ``messages[head_end:tail_start]`` into a marker; assemble result.

    Slices by ``bounds``, runs the gated summarizer over the dropped middle,
    builds the marker, and assembles ``head | marker | [todo_snapshot] |
    [search breadcrumbs] | tail``. Returns ``(result, summary_text)``;
    ``summary_text`` is None when the summarizer fell back to a static marker.

    Does NOT write runtime — the caller commits via ``commit_compaction`` as
    its last step (Task-3 invariant: any exception before commit leaves
    runtime untouched).
    """
    head_end, tail_start, _ = bounds
    head = messages[:head_end]
    dropped = messages[head_end:tail_start]
    tail = messages[tail_start:]
    summary_text = await _gated_summarize_or_none(ctx, dropped, focus=focus)
    has_tail = len(tail) > 0
    marker = build_compaction_marker(len(dropped), summary_text, has_tail=has_tail)
    todo_snapshot = build_todo_snapshot(ctx.deps.session.session_todos)
    result = [
        *head,
        marker,
        *([todo_snapshot] if todo_snapshot is not None else []),
        *_preserve_search_tool_breadcrumbs(dropped),
        *tail,
    ]
    return result, summary_text


def commit_compaction(
    ctx: RunContext[CoDeps],
    result: list[ModelMessage],
) -> None:
    """Atomically write the three runtime "applied" fields.

    Single writer of ``compaction_applied_this_turn``,
    ``post_compaction_token_estimate``, and ``message_count_at_last_compaction``.
    Token estimate is computed before any write so a token-estimator failure
    leaves runtime untouched (partial-commit prevention).

    Callers must invoke this as the last step before returning to preserve the
    Task-3 invariant.
    """
    post_token_estimate = estimate_message_tokens(result)
    ctx.deps.runtime.compaction_applied_this_turn = True
    ctx.deps.runtime.post_compaction_token_estimate = post_token_estimate
    ctx.deps.runtime.message_count_at_last_compaction = len(result)


def _record_proactive_outcome(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    result: list[ModelMessage],
    summary_text: str | None,
    *,
    token_count: int,
) -> None:
    """Apply proactive-specific post-compaction policy.

    Fires the closing status callback (3-way: success / no-model / summarizer-
    failed), computes savings using ``token_count`` (the trigger's
    ``max(local, reported)``), emits execution OTEL attributes onto the
    wrapper span, updates the anti-thrash counter, then commits runtime as
    the final step. Any exception before ``commit_compaction`` leaves runtime
    untouched (Task-3 invariant).
    """
    cfg = ctx.deps.config.compaction

    if (cb := ctx.deps.runtime.status_callback) is not None:
        if summary_text is not None:
            cb("Compacted.")
        elif ctx.deps.model is None:
            cb("LLM compaction unavailable — used static marker.")
        else:
            cb("Summarizer failed — used static marker.")

    tokens_after = estimate_message_tokens(result)
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

    commit_compaction(ctx, result)


async def recover_overflow_history(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage] | None:
    """Single-tier overflow recovery: strip-then-summarize.

    PATH 1: strip every ToolReturnPart to a per-tool semantic marker (no
            ``COMPACTABLE_TOOLS`` filter, no recency cap, no boundary). If the
            stripped history fits the budget, return it directly — no LLM call.

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
    bounds = plan_compaction_boundaries(stripped, budget, cfg.tail_fraction)
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
                if isinstance(part, UserPromptPart):
                    return part.content[-200:]
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
    yield runs; once tripped, the system stops auto-compacting and surfaces
    a user-actionable hint pointing at /compact and /new.

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

        # --- guard resolution ---
        guard_active = False
        guard_cleared = False
        fresh_responses_after_compact = 0

        if ctx.deps.runtime.compaction_applied_this_turn:
            reported = 0
        elif ctx.deps.runtime.post_compaction_token_estimate is not None:
            guard_active = True
            _count = ctx.deps.runtime.message_count_at_last_compaction
            # Only clear when a ModelResponse that actually saw the post-compaction
            # history exists. Message-count delta alone is unreliable: the +2 messages
            # may have been generated from the pre-compaction context (tool returns,
            # user messages) with no fresh LLM call in between.
            if _count is not None:
                fresh_responses_after_compact = sum(
                    1
                    for m in messages[_count:]
                    if isinstance(m, ModelResponse) and m.usage.input_tokens > 0
                )
                if fresh_responses_after_compact > 0:
                    guard_cleared = True
                    ctx.deps.runtime.post_compaction_token_estimate = None
                    ctx.deps.runtime.message_count_at_last_compaction = None
                    reported = latest_response_input_tokens(messages)
                else:
                    reported = ctx.deps.runtime.post_compaction_token_estimate
            else:
                reported = ctx.deps.runtime.post_compaction_token_estimate
        else:
            reported = latest_response_input_tokens(messages)

        # Trigger uses max(local, reported) to bias toward earlier compaction.
        # Savings uses the same effective-before so reported-driven triggers don't
        # appear low-yield when local estimate is near the post-compaction value.
        tokens_before_local = estimate_message_tokens(messages)
        token_count = max(tokens_before_local, reported)
        token_threshold = int(budget * cfg.compaction_ratio)

        # Count tool calls in current history for OTEL diagnostics.
        tool_calls_in_history = sum(
            1
            for m in messages
            if isinstance(m, ModelResponse)
            for p in m.parts
            if isinstance(p, ToolCallPart)
        )

        log.debug(
            "Proactive check: msgs=%d local=%d reported=%d count=%d threshold=%d "
            "guard=%s cleared=%s fresh_resp=%d tool_calls=%d",
            len(messages),
            tokens_before_local,
            reported,
            token_count,
            token_threshold,
            guard_active,
            guard_cleared,
            fresh_responses_after_compact,
            tool_calls_in_history,
        )

        span = current_span()
        span.set_attribute("compaction.msgs", len(messages))
        span.set_attribute("compaction.local_tokens", tokens_before_local)
        span.set_attribute("compaction.reported_tokens", reported)
        span.set_attribute("compaction.token_count", token_count)
        span.set_attribute("compaction.threshold", token_threshold)
        span.set_attribute("compaction.budget", budget)
        span.set_attribute("compaction.guard_active", guard_active)
        span.set_attribute("compaction.guard_cleared", guard_cleared)
        span.set_attribute(
            "compaction.fresh_responses_after_compact", fresh_responses_after_compact
        )
        span.set_attribute("compaction.tool_calls_in_history", tool_calls_in_history)
        span.set_attribute(
            "compaction.applied_this_turn", ctx.deps.runtime.compaction_applied_this_turn
        )

        from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN

        span.set_attribute("compaction.tool_call_limit", MAX_TOOL_CALLS_PER_MODEL_TURN)
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

        if (
            ctx.deps.runtime.consecutive_low_yield_proactive_compactions
            >= cfg.proactive_thrash_window
        ):
            log.info("Compaction: anti-thrashing gate active, skipping")
            span.set_attribute("compaction.fired", False)
            span.set_attribute("compaction.skip_reason", "anti_thrash_gate")
            return messages

        bounds = plan_compaction_boundaries(messages, budget, cfg.tail_fraction)
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
        result, summary_text = await compact_messages(ctx, messages, bounds, focus=focus)
        if summary_text is not None:
            log.info("Sliding window: summarised %d messages inline", dropped_count)

        _record_proactive_outcome(ctx, messages, result, summary_text, token_count=token_count)
        return result
    except Exception:
        log.warning("Mid-turn compaction failed — skipping", exc_info=True)
        return messages
