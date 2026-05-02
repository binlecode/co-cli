"""Public compaction entry points: summarization, overflow recovery, pre-turn hygiene.

Imported outside ``co_cli/context/`` (agent/, commands/, prompts/, orchestrate)
so this module is package-public. Implementation details — turn grouping,
boundary planning, marker builders, enrichment gathering, and history
processors — live in package-private siblings and are re-exported here so
external callers have a single import surface.

Submodule map:
    _compaction_boundaries  — TurnGroup, group_by_turn, plan_compaction_boundaries
    _compaction_markers     — static/summary/todo markers, enrichment context
    _history_processors     — dedup_tool_results, evict_old_tool_results, evict_batch_tool_outputs
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
from co_cli.context._history_processors import (
    COMPACTABLE_KEEP_RECENT,
    dedup_tool_results,
    evict_batch_tool_outputs,
    evict_old_tool_results,
)
from co_cli.context._http_error_classifier import is_context_overflow
from co_cli.context.summarization import (
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.deps import CoDeps

__all__ = [
    "COMPACTABLE_KEEP_RECENT",
    "STATIC_MARKER_PREFIX",
    "SUMMARY_MARKER_PREFIX",
    "TODO_SNAPSHOT_PREFIX",
    "CompactionBoundaries",
    "TurnGroup",
    "apply_compaction",
    "build_compaction_marker",
    "build_todo_snapshot",
    "dedup_tool_results",
    "emergency_recover_overflow_history",
    "estimate_message_tokens",
    "evict_batch_tool_outputs",
    "evict_old_tool_results",
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

    Both reactive recovery paths (planner-based and emergency structural) reset
    these unconditionally — overflow already proves the system needed to compact,
    so crediting it as a clean resync prevents the gate from staying tripped and
    suppressing the next proactive run.  The hint re-arms with the counter per
    the banner-text contract.
    """
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0
    ctx.deps.runtime.compaction_thrash_hint_emitted = False


_COMPACTION_BREAKER_TRIP: int = 3
"""Consecutive summarization failures that trip the circuit breaker."""

_COMPACTION_BREAKER_PROBE_EVERY: int = 10
"""Once tripped, allow one LLM probe attempt every N blocked calls.
First probe fires at skip_count == TRIP + PROBE_EVERY (i.e. 13), then every
PROBE_EVERY counts thereafter (23, 33, …). A successful probe resets the counter.
"""


def _summarization_gate_open(ctx: RunContext[CoDeps]) -> bool:
    """Decide whether the LLM summarizer may run for the next compaction pass.

    Returns False when the model is absent or the circuit breaker requires a skip;
    returns True otherwise. Increments ``compaction_skip_count`` on a breaker-blocked
    skip so the existing probe cadence is preserved. The caller resets the count on a
    successful summary and increments it on summarizer failure.
    """
    if not ctx.deps.model:
        log.info("Compaction: model absent, using static marker")
        return False

    count = ctx.deps.runtime.compaction_skip_count
    # skips_since_trip == 0 blocks the initial trip; probes fire every PROBE_EVERY skips after that
    skips_since_trip = count - _COMPACTION_BREAKER_TRIP
    if count >= _COMPACTION_BREAKER_TRIP and (
        skips_since_trip == 0 or skips_since_trip % _COMPACTION_BREAKER_PROBE_EVERY != 0
    ):
        log.warning("Compaction: circuit breaker active (count=%d), static marker", count)
        ctx.deps.runtime.compaction_skip_count += 1
        return False
    if count >= _COMPACTION_BREAKER_TRIP:
        log.info("Compaction: circuit breaker probe (count=%d)", count)
    return True


async def summarize_dropped_messages(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
    *,
    focus: str | None = None,
    previous_summary: str | None = None,
) -> str:
    """Pure summarizer call over ``dropped`` — no gate, no fallback.

    Callers must call ``_summarization_gate_open(ctx)`` first; this function assumes
    a model is configured and the circuit breaker permits the LLM call. Raises on
    summarizer failure (including ``asyncio.CancelledError``).
    """
    enrichment = gather_compaction_context(ctx, dropped)
    return await summarize_messages(
        ctx.deps,
        dropped,
        personality_active=bool(ctx.deps.config.personality),
        context=enrichment,
        focus=focus,
        previous_summary=previous_summary,
    )


def _is_valid_summary(text: str | None) -> bool:
    """Rejects empty/whitespace-only output; accepts any non-empty string."""
    return bool(text and text.strip())


async def _gated_summarize_or_none(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
    *,
    announce: bool,
    focus: str | None,
    previous_summary: str | None = None,
) -> str | None:
    """Run the summarizer if the gate is open, else return None.

    Owns the user-visible "Compacting conversation..." announce print, the success
    reset of ``compaction_skip_count`` on a valid (non-empty) summary, and the
    fall-through-to-static-marker path when the summarizer raises or returns empty.
    Lets ``asyncio.CancelledError`` propagate.
    """
    if not _summarization_gate_open(ctx):
        return None

    if announce:
        from co_cli.display.core import console

        console.print("[dim]Compacting conversation...[/dim]")

    try:
        summary_text = await summarize_dropped_messages(
            ctx, dropped, focus=focus, previous_summary=previous_summary
        )
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


async def apply_compaction(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    bounds: CompactionBoundaries,
    *,
    announce: bool,
    focus: str | None = None,
) -> tuple[list[ModelMessage], str | None]:
    """Assemble a compacted history from bounds and set runtime flags.

    Bounds may come from ``plan_compaction_boundaries`` (automatic compaction) or
    represent a full-history replacement ``(0, n, n)`` (manual ``/compact``). When
    summarization is unavailable (no model, circuit breaker tripped, or LLM
    failure), assembly continues with a static marker.

    Returns ``(result, summary_text)``. ``summary_text`` is None when the
    summarizer fell back to a static marker, letting callers log success
    conditionally.
    """
    head_end, tail_start, dropped_count = bounds
    dropped = messages[head_end:tail_start]
    previous_summary = ctx.deps.runtime.previous_compaction_summary
    summary_text = await _gated_summarize_or_none(
        ctx, dropped, announce=announce, focus=focus, previous_summary=previous_summary
    )
    if summary_text is not None:
        ctx.deps.runtime.previous_compaction_summary = summary_text
    marker = build_compaction_marker(dropped_count, summary_text)
    todo_snapshot = build_todo_snapshot(ctx.deps.session.session_todos)
    result = [
        *messages[:head_end],
        marker,
        *([todo_snapshot] if todo_snapshot is not None else []),
        *_preserve_search_tool_breadcrumbs(dropped),
        *messages[tail_start:],
    ]
    ctx.deps.runtime.compaction_applied_this_turn = True
    ctx.deps.runtime.post_compaction_token_estimate = estimate_message_tokens(result)
    ctx.deps.runtime.message_count_at_last_compaction = len(result)
    return result, summary_text


def _effective_token_count(messages: list[ModelMessage], reported: int) -> int:
    """Token count for threshold checks: max of local estimate and provider-reported.

    Two signals, neither fully trustworthy: the local char-based estimate
    can drift from provider tokenization, and the provider-reported count
    lags by a turn. Taking the max biases toward earlier compaction — safer
    than under-counting.
    """
    return max(estimate_message_tokens(messages), reported)


async def recover_overflow_history(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage] | None:
    """Recover from provider context overflow via the shared boundary planner.

    Calls ``plan_compaction_boundaries`` with config-sourced tail settings.
    The last turn group is always preserved via ``_MIN_RETAINED_TURN_GROUPS=1``.
    Returns None when no compaction boundary exists — caller should fall back to
    ``emergency_recover_overflow_history``.
    """
    ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
    cfg = ctx.deps.config.compaction
    bounds = plan_compaction_boundaries(
        messages,
        budget,
        cfg.tail_fraction,
    )
    if bounds is None:
        log.warning(
            "Compaction: overflow recovery boundary planning returned None. "
            "budget=%d tail_fraction=%.2f token_count=%d — caller must try emergency fallback.",
            budget,
            cfg.tail_fraction,
            _effective_token_count(messages, latest_response_input_tokens(messages)),
        )
        return None

    result, _ = await apply_compaction(ctx, messages, bounds, announce=False)
    _reset_thrash_state(ctx)
    return result


async def emergency_recover_overflow_history(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage] | None:
    """Structural last-resort overflow recovery — no planner, no LLM.

    Drops all middle turn groups, keeping first + static marker + last. Preserves
    the non-LLM continuity state that the planner-based path preserves: the todo
    snapshot from session state and search_tools breadcrumbs from the dropped
    range. Used when ``recover_overflow_history`` returns None despite a provider
    overflow rejection (estimator underestimate; the planner sees no work to do).
    Returns None when ``len(groups) <= 2`` — the pre-existing structural
    first-turn-overflow limit.
    """
    groups = group_by_turn(messages)
    if len(groups) <= 2:
        return None
    dropped = groups_to_messages(groups[1:-1])
    todo_snapshot = build_todo_snapshot(ctx.deps.session.session_todos)
    result = [
        *groups_to_messages([groups[0]]),
        static_marker(len(dropped)),
        *([todo_snapshot] if todo_snapshot is not None else []),
        *_preserve_search_tool_breadcrumbs(dropped),
        *groups_to_messages([groups[-1]]),
    ]
    ctx.deps.runtime.compaction_applied_this_turn = True
    ctx.deps.runtime.post_compaction_token_estimate = estimate_message_tokens(result)
    ctx.deps.runtime.message_count_at_last_compaction = len(result)
    _reset_thrash_state(ctx)
    log.warning(
        "Emergency overflow recovery: planner returned None; dropped all middle groups "
        "(len(groups)=%d).",
        len(groups),
    )
    return result


async def _run_window_compaction(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    budget: int,
) -> list[ModelMessage] | None:
    """Plan boundaries and apply compaction.

    Called after the caller's gate checks have already passed. Returns the
    original ``messages`` reference unchanged when boundary planning fails;
    otherwise returns a freshly-constructed list. Per-layer state updates
    (e.g. proactive thrash tracking) are the caller's responsibility.
    """
    cfg = ctx.deps.config.compaction
    bounds = plan_compaction_boundaries(messages, budget, cfg.tail_fraction)
    if bounds is None:
        log.warning(
            "Compaction: boundary planning returned None. "
            "budget=%d tail_fraction=%.2f — no compaction possible.",
            budget,
            cfg.tail_fraction,
        )
        return None

    _, _, dropped_count = bounds
    result, summary_text = await apply_compaction(ctx, messages, bounds, announce=True)
    if summary_text is not None:
        log.info("Sliding window: summarised %d messages inline", dropped_count)
    return result


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
        ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
        budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
        if budget <= 0:
            return messages
        cfg = ctx.deps.config.compaction

        if ctx.deps.runtime.compaction_applied_this_turn:
            reported = 0
        elif ctx.deps.runtime.post_compaction_token_estimate is not None:
            _count = ctx.deps.runtime.message_count_at_last_compaction
            if _count is not None and len(messages) >= _count + 2:
                # New ModelRequest + ModelResponse have landed — fresh provider count available.
                ctx.deps.runtime.post_compaction_token_estimate = None
                ctx.deps.runtime.message_count_at_last_compaction = None
                reported = latest_response_input_tokens(messages)
            else:
                reported = ctx.deps.runtime.post_compaction_token_estimate
        else:
            reported = latest_response_input_tokens(messages)
        # Trigger threshold uses max(local, reported) — biases toward earlier compaction.
        # Savings ratio uses local-only on both sides — apples-to-apples yield comparison.
        tokens_before_local = estimate_message_tokens(messages)
        token_count = max(tokens_before_local, reported)
        token_threshold = int(budget * cfg.compaction_ratio)

        if token_count <= token_threshold:
            return messages

        if (
            ctx.deps.runtime.consecutive_low_yield_proactive_compactions
            >= cfg.proactive_thrash_window
        ):
            log.info("Compaction: anti-thrashing gate active, skipping")
            if not ctx.deps.runtime.compaction_thrash_hint_emitted:
                from co_cli.display.core import console

                console.print(
                    "[dim]Compaction paused: recent passes freed too little context. "
                    "/compact to force one more pass, or /new for a fresh session.[/dim]"
                )
                ctx.deps.runtime.compaction_thrash_hint_emitted = True
            return messages

        result = await _run_window_compaction(ctx, messages, budget)
        if result is None:
            return messages

        tokens_after_local = estimate_message_tokens(result)
        savings = (
            (tokens_before_local - tokens_after_local) / tokens_before_local
            if tokens_before_local > 0
            else 0.0
        )
        if savings < cfg.min_proactive_savings:
            ctx.deps.runtime.consecutive_low_yield_proactive_compactions += 1
        else:
            # hint re-arms with counter — banner-text contract
            ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0
            ctx.deps.runtime.compaction_thrash_hint_emitted = False
        return result
    except Exception:
        log.warning("Mid-turn compaction failed — skipping", exc_info=True)
        return messages
