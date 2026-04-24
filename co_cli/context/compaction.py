"""Public compaction entry points: summarization, overflow recovery, pre-turn hygiene.

Imported outside ``co_cli/context/`` (agent/, commands/, prompts/, orchestrate)
so this module is package-public. Implementation details — turn grouping,
boundary planning, marker builders, enrichment gathering, and history
processors — live in package-private siblings and are re-exported here so
external callers have a single import surface.

Submodule map:
    _compaction_boundaries  — TurnGroup, group_by_turn, plan_compaction_boundaries
    _compaction_markers     — static/summary/todo markers, enrichment context
    _history_processors     — dedup_tool_results, truncate_tool_results, enforce_batch_budget
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage

from co_cli.context._compaction_boundaries import (
    TurnGroup,
    _CompactionBoundaries,
    find_first_run_end,
    group_by_turn,
    groups_to_messages,
    plan_compaction_boundaries,
)
from co_cli.context._compaction_markers import (
    SUMMARY_MARKER_PREFIX,
    TODO_SNAPSHOT_PREFIX,
    build_compaction_marker,
    build_todo_snapshot,
    gather_compaction_context,
    static_marker,
    summary_marker,
)
from co_cli.context._history_processors import (
    COMPACTABLE_KEEP_RECENT,
    dedup_tool_results,
    enforce_batch_budget,
    truncate_tool_results,
)
from co_cli.context.summarization import (
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.deps import CoDeps

__all__ = [
    "COMPACTABLE_KEEP_RECENT",
    "SUMMARY_MARKER_PREFIX",
    "TODO_SNAPSHOT_PREFIX",
    "TurnGroup",
    "build_compaction_marker",
    "build_todo_snapshot",
    "dedup_tool_results",
    "emergency_compact",
    "emergency_recover_overflow_history",
    "enforce_batch_budget",
    "find_first_run_end",
    "gather_compaction_context",
    "group_by_turn",
    "groups_to_messages",
    "maybe_run_pre_turn_hygiene",
    "plan_compaction_boundaries",
    "recover_overflow_history",
    "static_marker",
    "summarize_dropped_messages",
    "summarize_history_window",
    "summary_marker",
    "truncate_tool_results",
]

log = logging.getLogger(__name__)


_CIRCUIT_BREAKER_PROBE_EVERY: int = 10
"""When the circuit breaker is tripped (failure_count >= 3), attempt the LLM anyway
every Nth subsequent trigger. A success resets the counter to 0. Prevents
permanent bypass from a transient provider hiccup that happened to hit 3 in a
row early in the session. First probe fires at failure_count == 3 + N (i.e. after
N skips), then every N skips thereafter.
"""


def emergency_compact(messages: list[ModelMessage]) -> list[ModelMessage] | None:
    """Static emergency compaction for overflow recovery — no LLM call.

    Keeps first group + last group + static marker between.
    Returns None if ≤2 groups (nothing to compact).
    """
    groups = group_by_turn(messages)
    if len(groups) <= 2:
        return None
    dropped_count = sum(len(g.messages) for g in groups[1:-1])
    return [
        *groups_to_messages([groups[0]]),
        static_marker(dropped_count),
        *groups_to_messages([groups[-1]]),
    ]


def _circuit_breaker_should_skip(failure_count: int) -> bool:
    """Return True when the circuit breaker requires a skip at this failure count.

    Trips at failure_count >= 3. Once tripped, allows a probe every
    _CIRCUIT_BREAKER_PROBE_EVERY skips: first probe at failure_count == 13,
    then 23, 33, and so on. At any other tripped count, callers must skip.
    """
    if failure_count < 3:
        return False
    skips_since_trip = failure_count - 3
    return skips_since_trip == 0 or skips_since_trip % _CIRCUIT_BREAKER_PROBE_EVERY != 0


async def summarize_dropped_messages(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
    *,
    announce: bool,
) -> str | None:
    """Summarize dropped messages when the model and circuit breaker allow it."""
    if not ctx.deps.model:
        log.info("Compaction: model absent, using static marker")
        return None

    count = ctx.deps.runtime.compaction_failure_count
    if _circuit_breaker_should_skip(count):
        log.warning("Compaction: circuit breaker active (count=%d), static marker", count)
        ctx.deps.runtime.compaction_failure_count += 1
        return None
    if count >= 3:
        log.info("Compaction: circuit breaker probe (count=%d)", count)

    if announce:
        from co_cli.display._core import console

        console.print("[dim]Compacting conversation...[/dim]")

    try:
        enrichment = gather_compaction_context(ctx, dropped)
        summary_text = await summarize_messages(
            ctx.deps,
            dropped,
            personality_active=bool(ctx.deps.config.personality),
            context=enrichment,
        )
        ctx.deps.runtime.compaction_failure_count = 0
        return summary_text
    except Exception:
        log.warning(
            "Compaction summarization failed — falling back to static marker", exc_info=True
        )
        ctx.deps.runtime.compaction_failure_count += 1
        return None


def _preserve_search_tool_breadcrumbs(
    dropped: list[ModelMessage],
) -> list[ModelMessage]:
    """Keep SDK search-tools discovery state across compaction boundaries."""
    return [
        msg
        for msg in dropped
        if isinstance(msg, ModelRequest)
        and any(isinstance(p, ToolReturnPart) and p.tool_name == "search_tools" for p in msg.parts)
    ]


async def _apply_compaction(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    bounds: _CompactionBoundaries,
    *,
    announce: bool,
) -> tuple[list[ModelMessage], str | None]:
    """Assemble a compacted history from planner bounds and set runtime flags.

    Returns ``(result, summary_text)``. ``summary_text`` is None when the
    summarizer fell back to a static marker (model absent, circuit breaker
    tripped, or LLM failure), letting callers log success conditionally.
    """
    head_end, tail_start, dropped_count = bounds
    dropped = messages[head_end:tail_start]
    summary_text = await summarize_dropped_messages(ctx, dropped, announce=announce)
    marker = build_compaction_marker(dropped_count, summary_text)
    todo_snapshot = build_todo_snapshot(ctx.deps.session.session_todos)
    ctx.deps.runtime.history_compaction_applied = True
    ctx.deps.runtime.compacted_in_current_turn = True
    result = [
        *messages[:head_end],
        marker,
        *([todo_snapshot] if todo_snapshot is not None else []),
        *_preserve_search_tool_breadcrumbs(dropped),
        *messages[tail_start:],
    ]
    from co_cli.knowledge._distiller import schedule_compaction_extraction

    schedule_compaction_extraction(messages, result, ctx.deps)
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
    token_count = _effective_token_count(messages, latest_response_input_tokens(messages))
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
            token_count,
        )
        return None

    result, _ = await _apply_compaction(ctx, messages, bounds, announce=False)
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
    ctx.deps.runtime.history_compaction_applied = True
    ctx.deps.runtime.compacted_in_current_turn = True
    log.warning(
        "Emergency overflow recovery: planner returned None; dropped all middle groups "
        "(len(groups)=%d).",
        len(groups),
    )
    from co_cli.knowledge._distiller import schedule_compaction_extraction

    schedule_compaction_extraction(messages, result, ctx.deps)
    return result


async def summarize_history_window(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Drop middle messages when history exceeds the token budget threshold.

    Triggers when ``token_count > max(int(budget * cfg.proactive_ratio), cfg.min_context_length_tokens)``.
    Boundaries come from ``plan_compaction_boundaries`` — the same planner
    used by overflow recovery. Anti-thrashing gate skips proactive when the
    last N proactive runs each yielded < cfg.min_proactive_savings savings.

    Keeps:
      - **head** — first run's messages (up to first TextPart response)
      - **tail** — planner-selected suffix bounded by ``cfg.tail_fraction * budget``
    Drops:
      - everything in between, replaced by an inline LLM summary when
        possible, else a static marker (circuit-breaker fallback)

    Summarisation runs inline via ``summarize_messages()`` when compaction
    triggers. When ``deps.model`` is absent (sub-agent context) or the
    circuit breaker is tripped (3+ consecutive failures), falls back to a
    static marker without attempting an LLM call.

    Registered as the last history processor.
    """
    ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
    cfg = ctx.deps.config.compaction

    reported = (
        0 if ctx.deps.runtime.compacted_in_current_turn else latest_response_input_tokens(messages)
    )
    estimate = estimate_message_tokens(messages)
    token_count = max(estimate, reported)
    token_threshold = max(int(budget * cfg.proactive_ratio), cfg.min_context_length_tokens)

    if token_count <= token_threshold:
        return messages

    if ctx.deps.runtime.consecutive_low_yield_proactive_compactions >= cfg.proactive_thrash_window:
        log.info("Compaction: proactive anti-thrashing gate active, skipping")
        return messages

    bounds = plan_compaction_boundaries(
        messages,
        budget,
        cfg.tail_fraction,
    )
    if bounds is None:
        log.warning(
            "Compaction: proactive boundary planning returned None "
            "(tail group exceeds budget). token_count=%d budget=%d tail_fraction=%.2f — no compaction possible.",
            token_count,
            budget,
            cfg.tail_fraction,
        )
        return messages

    dropped_count = bounds[2]
    result, summary_text = await _apply_compaction(ctx, messages, bounds, announce=True)
    if summary_text is not None:
        log.info("Sliding window: summarised %d messages inline", dropped_count)

    tokens_after = estimate_message_tokens(result)
    savings = (token_count - tokens_after) / token_count if token_count > 0 else 0.0
    if savings < cfg.min_proactive_savings:
        ctx.deps.runtime.consecutive_low_yield_proactive_compactions += 1
    else:
        ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0

    return result


async def maybe_run_pre_turn_hygiene(
    deps: CoDeps,
    message_history: list[ModelMessage],
    reported_input_tokens: int = 0,
) -> list[ModelMessage]:
    """Pre-turn hygiene compaction: compact if token count exceeds cfg.hygiene_ratio * budget.

    ``reported_input_tokens`` must be read from turn_usage before reset_for_turn() clears it.
    Sets deps.runtime.history_compaction_applied when compaction runs.
    Fails open: any exception returns message_history unchanged so the turn proceeds.
    """
    try:
        ctx_window = deps.model.context_window if deps.model else None
        budget = resolve_compaction_budget(deps.config, ctx_window)
        if budget <= 0:
            return message_history
        token_count = _effective_token_count(message_history, reported_input_tokens)
        token_threshold = max(
            int(budget * deps.config.compaction.hygiene_ratio),
            deps.config.compaction.min_context_length_tokens,
        )
        if token_count <= token_threshold:
            return message_history
        deps.runtime.consecutive_low_yield_proactive_compactions = 0
        raw_model = deps.model.model if deps.model else None
        ctx = RunContext(deps=deps, model=raw_model, usage=RunUsage())
        return await summarize_history_window(ctx, message_history)
    except Exception:
        log.warning("Pre-turn hygiene compaction failed — skipping", exc_info=True)
        return message_history
