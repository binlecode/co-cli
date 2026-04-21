"""History processors and preflight helpers for automatic context governance.

History processors are chained via ``Agent(history_processors=[...])``. They run
before every model request and transform the message list in-place.

Registered processors (pure transformers — no deps mutation):
    truncate_tool_results        — sync, content-clears compactable tool results by recency
    enforce_batch_budget         — sync, spills largest non-persisted tool returns when batch aggregate exceeds threshold
    compact_assistant_responses  — sync, caps large TextPart/ThinkingPart in older ModelResponse
    summarize_history_window     — async, summarizes middle messages via inline LLM or static marker

Preflight callables (called explicitly by run_turn() before model-bound segments):
    build_recall_injection       — async, returns date + personality + recall injection and recall-fired flag
    build_safety_injection       — sync, returns doom-loop / shell-reflection injection messages and flags
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli.context.summarization import (
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.context.tool_categories import COMPACTABLE_TOOLS, FILE_TOOLS
from co_cli.context.types import MemoryRecallState, SafetyState
from co_cli.deps import CoDeps

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class TurnGroup:
    """A contiguous group of messages forming one user turn.

    Boundary detection: a new group starts at each ``ModelRequest`` containing
    a ``UserPromptPart``.  Messages before the first such boundary form group 0.
    """

    messages: list[ModelMessage]
    start_index: int


_CompactionBoundaries = tuple[int, int, int]
"""(head_end, tail_start, dropped_count) — planner callers receive ``| None`` when no valid boundary exists."""


# ---------------------------------------------------------------------------
# Compaction constants
# ---------------------------------------------------------------------------

_CIRCUIT_BREAKER_PROBE_EVERY: int = 10
"""When the circuit breaker is tripped (failure_count >= 3), attempt the LLM anyway
every Nth subsequent trigger. A success resets the counter to 0. Prevents
permanent bypass from a transient provider hiccup that happened to hit 3 in a
row early in the session. First probe fires at failure_count == 3 + N (i.e. after
N skips), then every N skips thereafter.
"""

_MIN_RETAINED_TURN_GROUPS: int = 1
"""Minimum number of turn groups the planner must retain in the tail.

Hardcoded correctness invariant — setting it to 0 breaks the planner.
Not user-configurable. The soft-overrun multiplier allows the retained
tail to exceed ``tail_fraction * budget`` when needed to satisfy this floor.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn_group(msgs: list[ModelMessage], start: int) -> TurnGroup:
    """Construct a TurnGroup from a contiguous slice of messages."""
    return TurnGroup(messages=list(msgs), start_index=start)


def group_by_turn(messages: list[ModelMessage]) -> list[TurnGroup]:
    """Group messages into turn-sized units at ``UserPromptPart`` boundaries.

    A new group starts at each ``ModelRequest`` that contains a
    ``UserPromptPart`` (not just ``ToolReturnPart``).  Messages before the
    first such boundary form group 0.
    """
    if not messages:
        return []

    groups: list[TurnGroup] = []
    current_msgs: list[ModelMessage] = []
    current_start: int = 0

    for i, msg in enumerate(messages):
        is_boundary = isinstance(msg, ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in msg.parts
        )
        if is_boundary and current_msgs:
            groups.append(_make_turn_group(current_msgs, current_start))
            current_msgs = []
            current_start = i
        current_msgs.append(msg)

    if current_msgs:
        groups.append(_make_turn_group(current_msgs, current_start))

    return groups


def groups_to_messages(groups: list[TurnGroup]) -> list[ModelMessage]:
    """Flatten turn groups back to a message list."""
    result: list[ModelMessage] = []
    for group in groups:
        result.extend(group.messages)
    return result


def find_first_run_end(messages: list[ModelMessage]) -> int:
    """Return the index (inclusive) of the first ModelResponse with a TextPart or ThinkingPart.

    This anchors the "first run" boundary — everything up to and including
    this message belongs to the initial exchange that establishes session
    context.  If no such message exists, returns 0 (keep nothing pinned).

    ThinkingPart-only responses (extended thinking with no text) are accepted
    as valid anchors — they represent the first substantive model output and
    must not be dropped from the head.

    Design note: if the first ModelResponse is tool-only (no TextPart or
    ThinkingPart), this returns 0, so head_end=1 — only the initial
    ModelRequest is pinned. The first run's tool call/return cycle falls into
    the dropped middle section.
    """
    for i, msg in enumerate(messages):
        if isinstance(msg, ModelResponse) and any(
            isinstance(p, (TextPart, ThinkingPart)) for p in msg.parts
        ):
            return i
    return 0


def _static_marker(dropped_count: int) -> ModelRequest:
    """Build a structurally valid placeholder for dropped messages."""
    return ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    f"This session is being continued from a previous conversation "
                    f"that ran out of context. {dropped_count} earlier messages were "
                    "removed. Recent messages are preserved verbatim."
                ),
            ),
        ]
    )


def _summary_marker(dropped_count: int, summary_text: str) -> ModelRequest:
    """Build a structurally valid summary marker for compacted messages."""
    return ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    "This session is being continued from a previous conversation "
                    "that ran out of context. The summary below covers the earlier "
                    f"portion ({dropped_count} messages).\n\n{summary_text}\n\n"
                    "Recent messages are preserved verbatim."
                ),
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Shared boundary planner
# ---------------------------------------------------------------------------


def _anchor_tail_to_last_user(
    messages: list[ModelMessage],
    groups: list[TurnGroup],
    head_end: int,
    tail_start: int,
) -> int:
    """Extend tail_start back so the latest UserPromptPart is in the retained tail.

    If the latest UserPromptPart is already in the tail or head, returns tail_start
    unchanged (no-op). If it falls in the dropped middle, returns the start index
    of the group containing it.
    """
    last_user_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, ModelRequest) and any(isinstance(p, UserPromptPart) for p in msg.parts):
            last_user_idx = idx
            break

    if head_end <= last_user_idx < tail_start:
        for group in groups:
            if group.start_index <= last_user_idx < group.start_index + len(group.messages):
                return group.start_index

    return tail_start


def plan_compaction_boundaries(
    messages: list[ModelMessage],
    budget: int,
    tail_fraction: float,
    *,
    tail_soft_overrun_multiplier: float = 1.25,
) -> _CompactionBoundaries | None:
    """Plan ``(head_end, tail_start, dropped_count)`` for a compaction pass.

    Algorithm:
      1. ``head_end = find_first_run_end(messages) + 1``
      2. ``groups = group_by_turn(messages)``; abort when
         ``len(groups) < _MIN_RETAINED_TURN_GROUPS + 1``.
      3. Walk groups from the end, accumulating token estimates. Stop BEFORE
         adding a group that would push accumulated tokens over
         ``tail_fraction * budget``, UNLESS fewer than ``_MIN_RETAINED_TURN_GROUPS``
         groups have been accumulated. In that case, allow up to
         ``tail_fraction * budget * tail_soft_overrun_multiplier``; if even the
         soft-overrun cap is exceeded, accept the overrun and log at info.
      4. ``tail_start = accumulated_groups[0].start_index``.
      5. Active-user anchoring: if the latest ``UserPromptPart`` falls in the
         dropped middle (between ``head_end`` and ``tail_start``), extend
         ``tail_start`` backward to the start of the group containing it.
      6. Abort when ``tail_start <= head_end`` (head/tail overlap — nothing to drop).

    Shared between proactive compaction (``summarize_history_window``) and
    overflow recovery (``recover_overflow_history``). ``_MIN_RETAINED_TURN_GROUPS=1``
    is a hardcoded correctness invariant: the last turn group is always kept even
    when its tokens alone exceed the tail budget.
    """
    if not messages:
        return None

    first_run_end = find_first_run_end(messages)
    head_end = first_run_end + 1

    groups = group_by_turn(messages)
    if len(groups) < _MIN_RETAINED_TURN_GROUPS + 1:
        return None

    tail_budget = tail_fraction * budget
    soft_overrun_budget = tail_budget * tail_soft_overrun_multiplier
    acc_groups: list[TurnGroup] = []
    acc_tokens = 0
    for group in reversed(groups):
        gt = estimate_message_tokens(group.messages)
        if len(acc_groups) >= _MIN_RETAINED_TURN_GROUPS and acc_tokens + gt > tail_budget:
            break
        if len(acc_groups) < _MIN_RETAINED_TURN_GROUPS and acc_tokens + gt > soft_overrun_budget:
            log.info(
                "Compaction: last group exceeds soft-overrun budget (group_tokens=%d, soft_budget=%.0f); accepting overrun",
                gt,
                soft_overrun_budget,
            )
        acc_groups.insert(0, group)
        acc_tokens += gt

    if not acc_groups:
        return None

    tail_start = acc_groups[0].start_index
    tail_start = _anchor_tail_to_last_user(messages, groups, head_end, tail_start)

    if tail_start <= head_end:
        return None
    return (head_end, tail_start, tail_start - head_end)


# ---------------------------------------------------------------------------
# Processor helpers (shared by #1 and #2)
# ---------------------------------------------------------------------------

OLDER_MSG_MAX_CHARS = 2_500

COMPACTABLE_KEEP_RECENT = 5
"""Keep the N most-recent tool returns per compactable tool type; clear older.

Borrowed from ``fork-claude-code/services/compact/timeBasedMCConfig.ts:33``
(``keepRecent: 5``). Not convergent across peers — codex, hermes, and
opencode do not have per-tool recency retention. Not tuned specifically for
co-cli's tool surface; revisit via ``evals/eval_compaction_quality.py`` if a
retention/fidelity tradeoff becomes measurable.
"""

_CLEARED_PLACEHOLDER = "[tool result cleared — older than 5 most recent calls]"


def _find_last_turn_start(messages: list[ModelMessage]) -> int:
    """Return the index of the last ModelRequest containing a UserPromptPart.

    Returns 0 when no such message exists (protect nothing — degenerate case).
    """
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in messages[i].parts
        ):
            return i
    return 0


def _truncate_proportional(text: str, max_chars: int, head_ratio: float = 0.20) -> str:
    """Truncate text keeping head(head_ratio) + tail(1-head_ratio) with a marker between."""
    if len(text) <= max_chars:
        return text
    marker = "\n[...truncated...]\n"
    available = max_chars - len(marker)
    if available <= 0:
        # max_chars too small for marker — hard truncate
        return text[:max_chars]
    head_size = int(available * head_ratio)
    tail_size = available - head_size
    return text[:head_size] + marker + text[-tail_size:]


# ---------------------------------------------------------------------------
# 1. truncate_tool_results (sync — no I/O)
# ---------------------------------------------------------------------------


def _build_keep_ids(older: list[ModelMessage]) -> set[int]:
    """Reverse scan: collect ids of the COMPACTABLE_KEEP_RECENT most recent parts per tool."""
    keep_ids: set[int] = set()
    seen_counts: dict[str, int] = {}
    for msg in reversed(older):
        if not isinstance(msg, ModelRequest):
            continue
        for part in reversed(msg.parts):
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name not in COMPACTABLE_TOOLS:
                continue
            count = seen_counts.get(part.tool_name, 0)
            if count < COMPACTABLE_KEEP_RECENT:
                keep_ids.add(id(part))
            seen_counts[part.tool_name] = count + 1
    return keep_ids


def truncate_tool_results(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Content-clear compactable tool results older than the 5 most recent per tool type.

    Protects the last turn (everything from the last UserPromptPart onward).
    Non-compactable tools pass through intact regardless of count.

    Registered as the *first* history processor — cheap in-memory work, no
    LLM call.  ``ctx`` is required by pydantic-ai's history processor
    signature but no config fields are accessed.
    """
    boundary = _find_last_turn_start(messages)
    if boundary == 0:
        return messages

    older = messages[:boundary]
    keep_ids = _build_keep_ids(older)

    # Forward pass: content-clear compactable parts not in keep_ids
    for msg in older:
        if not isinstance(msg, ModelRequest):
            continue
        new_parts: list = []
        msg_modified = False
        for part in msg.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name in COMPACTABLE_TOOLS
                and id(part) not in keep_ids
            ):
                new_parts.append(
                    ToolReturnPart(
                        tool_name=part.tool_name,
                        content=_CLEARED_PLACEHOLDER,
                        tool_call_id=part.tool_call_id,
                    )
                )
                msg_modified = True
            else:
                new_parts.append(part)
        if msg_modified:
            msg.parts = new_parts

    return messages


# ---------------------------------------------------------------------------
# 2. compact_assistant_responses (sync — no I/O)
# ---------------------------------------------------------------------------


def compact_assistant_responses(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Cap large TextPart/ThinkingPart in older ModelResponse messages.

    Protects the last turn (everything from the last UserPromptPart onward).
    Does NOT touch ToolCallPart (args are critical for file path extraction),
    ToolReturnPart, or UserPromptPart.  Mutates in-place — no list rebuild.
    """
    boundary = _find_last_turn_start(messages)
    if boundary == 0:
        return messages

    for msg in messages[:boundary]:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if (
                isinstance(part, (TextPart, ThinkingPart))
                and len(part.content) > OLDER_MSG_MAX_CHARS
            ):
                part.content = _truncate_proportional(part.content, OLDER_MSG_MAX_CHARS)

    return messages


# ---------------------------------------------------------------------------
# 3. enforce_batch_budget (sync — disk I/O via persist_if_oversized)
# ---------------------------------------------------------------------------


def _tool_return_part_size(part: ToolReturnPart) -> int:
    """Return the serialized char size of a ToolReturnPart's content."""
    if isinstance(part.content, str):
        return len(part.content)
    return len(json.dumps(part.content, default=str))


def _collect_batch_parts(
    messages: list[ModelMessage],
) -> list[tuple[int, int, ToolReturnPart]]:
    """Return (msg_idx, part_idx, part) for ToolReturnParts in the current batch.

    The current batch starts immediately after the last ModelResponse containing
    a ToolCallPart. Returns an empty list when no such response exists.
    """
    batch_start = len(messages)
    for idx in range(len(messages) - 1, -1, -1):
        if isinstance(messages[idx], ModelResponse) and any(
            isinstance(p, ToolCallPart) for p in messages[idx].parts
        ):
            batch_start = idx + 1
            break
    if batch_start >= len(messages):
        return []
    result: list[tuple[int, int, ToolReturnPart]] = []
    for msg_idx in range(batch_start, len(messages)):
        msg = messages[msg_idx]
        if isinstance(msg, ModelRequest):
            for part_idx, part in enumerate(msg.parts):
                if isinstance(part, ToolReturnPart):
                    result.append((msg_idx, part_idx, part))
    return result


def _apply_batch_replacements(
    messages: list[ModelMessage],
    replacements: dict[int, dict[int, str]],
) -> None:
    """Rebuild parts lists for messages in replacements, mutating messages in-place."""
    for msg_idx, part_replacements in replacements.items():
        msg = messages[msg_idx]
        if not isinstance(msg, ModelRequest):
            continue
        new_parts = []
        for part_idx, part in enumerate(msg.parts):
            if part_idx in part_replacements and isinstance(part, ToolReturnPart):
                new_parts.append(
                    ToolReturnPart(
                        tool_name=part.tool_name,
                        content=part_replacements[part_idx],
                        tool_call_id=part.tool_call_id,
                    )
                )
            else:
                new_parts.append(part)
        msg.parts = new_parts


def enforce_batch_budget(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Per-batch aggregate spill: evict largest non-persisted tool returns
    when aggregate content exceeds config.tools.batch_spill_chars.

    Identifies the current batch as the ToolReturnParts that follow the last
    ModelResponse with a ToolCallPart. Spills candidates largest-first via
    persist_if_oversized(max_size=0) until aggregate fits or no eligible
    candidates remain. Fails open: if persist_if_oversized returns unchanged
    content (OSError fallback), that candidate is skipped.

    Registered between truncate_tool_results and compact_assistant_responses.
    Not added to delegation sub-agent chains (short-lived, unnecessary overhead).
    """
    from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG, persist_if_oversized

    threshold = ctx.deps.config.tools.batch_spill_chars
    batch_parts = _collect_batch_parts(messages)
    if not batch_parts:
        return messages

    aggregate = sum(_tool_return_part_size(part) for _, _, part in batch_parts)
    if aggregate <= threshold:
        return messages

    candidates = [
        (msg_idx, part_idx, part)
        for msg_idx, part_idx, part in batch_parts
        if isinstance(part.content, str) and PERSISTED_OUTPUT_TAG not in part.content
    ]
    candidates.sort(key=lambda t: _tool_return_part_size(t[2]), reverse=True)

    replacements: dict[int, dict[int, str]] = {}
    for msg_idx, part_idx, part in candidates:
        if aggregate <= threshold:
            break
        old_size = _tool_return_part_size(part)
        spilled = persist_if_oversized(
            str(part.content),
            ctx.deps.tool_results_dir,
            part.tool_name,
            max_size=0,
        )
        if spilled != part.content:
            replacements.setdefault(msg_idx, {})[part_idx] = spilled
            aggregate -= old_size - len(spilled)

    if not replacements:
        return messages

    _apply_batch_replacements(messages, replacements)
    return messages


# ---------------------------------------------------------------------------
# Context enrichment for summarization
# ---------------------------------------------------------------------------

_CONTEXT_MAX_CHARS = 4_000
_SUMMARY_MARKER_PREFIX = "This session is being continued from a previous conversation that ran out of context. The summary below"


def _gather_file_paths(dropped: list[ModelMessage]) -> str | None:
    """Extract file working set from ToolCallPart.args in the dropped range.

    Scoped to ``dropped`` only — paths already visible in the preserved tail
    would duplicate in the enrichment and waste summarizer attention
    (Gap M regression guard). ``ToolCallPart.args`` is never truncated by
    processor #1 so the args of dropped calls are still readable here.
    """
    file_paths: set[str] = set()
    for msg in dropped:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name in FILE_TOOLS:
                    args = part.args_as_dict()
                    path = args.get("path") or args.get("file_path")
                    if path:
                        file_paths.add(path)
    return f"Files touched: {', '.join(sorted(file_paths)[:20])}" if file_paths else None


def _gather_session_todos(todos: list) -> str | None:
    """Format pending session todos for compaction context."""
    if not todos:
        return None
    pending = [t for t in todos if t.get("status") not in ("completed", "cancelled")]
    if not pending:
        return None
    todo_lines = [
        f"- [{t.get('status', 'pending')}] {t.get('content', '?')}" for t in pending[:10]
    ]
    return "Active tasks:\n" + "\n".join(todo_lines)


def _gather_prior_summaries(dropped: list[ModelMessage]) -> str | None:
    """Extract prior summary text from dropped messages."""
    summaries: list[str] = []
    for msg in dropped:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if (
                    isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                    and part.content.startswith(_SUMMARY_MARKER_PREFIX)
                ):
                    summaries.append(f"Prior summary:\n{part.content}")
    return "\n\n".join(summaries) if summaries else None


def _gather_compaction_context(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
) -> str | None:
    """Gather side-channel context for the summarizer from sources that survive truncation.

    Sources, all scoped to the dropped range or session state:
    1. File working set from ToolCallPart.args in ``dropped``
    2. Pending session todos from ``ctx.deps.session``
    3. Prior-summary text from ``dropped``

    Returns None when no context was gathered.
    """
    context_parts = [
        p
        for p in [
            _gather_file_paths(dropped),
            _gather_session_todos(ctx.deps.session.session_todos),
            _gather_prior_summaries(dropped),
        ]
        if p is not None
    ]
    if not context_parts:
        return None
    result = "\n\n".join(context_parts)
    return result[:_CONTEXT_MAX_CHARS] if len(result) > _CONTEXT_MAX_CHARS else result


# ---------------------------------------------------------------------------
# Emergency compaction (no LLM — used by overflow recovery)
# ---------------------------------------------------------------------------


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
        _static_marker(dropped_count),
        *groups_to_messages([groups[-1]]),
    ]


async def _summarize_dropped_messages(
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
    if count >= 3:
        skips_since_trip = count - 3
        if skips_since_trip == 0 or skips_since_trip % _CIRCUIT_BREAKER_PROBE_EVERY != 0:
            log.warning("Compaction: circuit breaker active (count=%d), static marker", count)
            ctx.deps.runtime.compaction_failure_count += 1
            return None
        log.info("Compaction: circuit breaker probe (count=%d)", count)

    if announce:
        from co_cli.display._core import console

        console.print("[dim]Compacting conversation...[/dim]")

    enrichment = _gather_compaction_context(ctx, dropped)
    try:
        summary_text = await summarize_messages(
            ctx.deps,
            dropped,
            personality_active=bool(ctx.deps.config.personality),
            context=enrichment,
        )
        ctx.deps.runtime.compaction_failure_count = 0
        return summary_text
    except (ModelHTTPError, ModelAPIError) as e:
        log.warning("Compaction summarization failed: %s", e)
        ctx.deps.runtime.compaction_failure_count += 1
        return None


def _preserve_search_tool_breadcrumbs(
    dropped: list[ModelMessage],
    kept_ids: set[int],
) -> list[ModelMessage]:
    """Keep SDK search-tools discovery state across compaction boundaries.

    Skips any message whose ``id(msg)`` is already in ``kept_ids`` — prevents
    quadratic accumulation when a prior compaction's preserved breadcrumb
    falls into a later compaction's head or tail range (Gap J regression
    guard). Callers build ``kept_ids`` from the head + tail slices before
    invoking.
    """
    return [
        msg
        for msg in dropped
        if id(msg) not in kept_ids
        and isinstance(msg, ModelRequest)
        and any(isinstance(p, ToolReturnPart) and p.tool_name == "search_tools" for p in msg.parts)
    ]


async def recover_overflow_history(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage] | None:
    """Recover from provider context overflow via the shared boundary planner.

    Calls ``plan_compaction_boundaries`` with config-sourced tail settings.
    The last turn group is always preserved via ``_MIN_RETAINED_TURN_GROUPS=1``.
    Returns None when no compaction boundary exists.
    """
    ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
    cfg = ctx.deps.config.compaction
    bounds = plan_compaction_boundaries(
        messages,
        budget,
        cfg.tail_fraction,
        tail_soft_overrun_multiplier=cfg.tail_soft_overrun_multiplier,
    )
    if bounds is None:
        return None

    head_end, tail_start, dropped_count = bounds
    head = messages[:head_end]
    tail = messages[tail_start:]
    dropped = messages[head_end:tail_start]
    kept_ids = {id(m) for m in head} | {id(m) for m in tail}

    summary_text = await _summarize_dropped_messages(ctx, dropped, announce=False)
    marker = (
        _summary_marker(dropped_count, summary_text)
        if summary_text is not None
        else _static_marker(dropped_count)
    )
    ctx.deps.runtime.history_compaction_applied = True
    return [*head, marker, *_preserve_search_tool_breadcrumbs(dropped, kept_ids), *tail]


# ---------------------------------------------------------------------------
# 5. summarize_history_window (async — LLM call)
# ---------------------------------------------------------------------------


async def summarize_history_window(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Drop middle messages when history exceeds the token budget threshold.

    Triggers when ``token_count > max(int(budget * cfg.proactive_ratio), cfg.min_threshold_tokens)``.
    Boundaries come from ``plan_compaction_boundaries`` — the same planner
    used by overflow recovery. Anti-thrashing gate skips proactive when the
    last N runs all yielded < cfg.min_proactive_savings savings.

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
    estimate = estimate_message_tokens(messages)
    reported = latest_response_input_tokens(messages)
    token_count = max(estimate, reported)
    ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
    cfg = ctx.deps.config.compaction
    token_threshold = max(int(budget * cfg.proactive_ratio), cfg.min_threshold_tokens)

    if token_count <= token_threshold:
        return messages

    # Anti-thrashing gate: skip proactive if the last N compactions all yielded low savings.
    # Does NOT gate overflow recovery or hygiene — only the proactive path.
    recent = ctx.deps.runtime.recent_proactive_savings
    if len(recent) >= cfg.proactive_thrash_window and all(
        s < cfg.min_proactive_savings for s in recent[-cfg.proactive_thrash_window :]
    ):
        log.info("Compaction: proactive anti-thrashing gate active, skipping")
        return messages

    bounds = plan_compaction_boundaries(
        messages,
        budget,
        cfg.tail_fraction,
        tail_soft_overrun_multiplier=cfg.tail_soft_overrun_multiplier,
    )
    if bounds is None:
        return messages

    head_end, tail_start, dropped_count = bounds
    head = messages[:head_end]
    tail = messages[tail_start:]
    dropped = messages[head_end:tail_start]
    kept_ids = {id(m) for m in head} | {id(m) for m in tail}

    summary_text = await _summarize_dropped_messages(ctx, dropped, announce=True)
    if summary_text is not None:
        summary_marker = _summary_marker(dropped_count, summary_text)
        log.info("Sliding window: summarised %d messages inline", dropped_count)
    else:
        summary_marker = _static_marker(dropped_count)

    ctx.deps.runtime.history_compaction_applied = True
    preserved_discovery = _preserve_search_tool_breadcrumbs(dropped, kept_ids)
    result = [*head, summary_marker, *preserved_discovery, *tail]

    # Track proactive compaction savings for the anti-thrashing gate.
    tokens_after = estimate_message_tokens(result)
    savings = (token_count - tokens_after) / token_count if token_count > 0 else 0.0
    updated_savings = list(ctx.deps.runtime.recent_proactive_savings)
    updated_savings.append(savings)
    ctx.deps.runtime.recent_proactive_savings = updated_savings[-cfg.proactive_thrash_window :]

    return result


# ---------------------------------------------------------------------------
# maybe_run_pre_turn_hygiene — pre-turn maintenance compaction
# ---------------------------------------------------------------------------


async def maybe_run_pre_turn_hygiene(
    deps: CoDeps,
    message_history: list[ModelMessage],
    model: Any,
) -> list[ModelMessage]:
    """Pre-turn hygiene compaction: compact if rough-estimate tokens exceed cfg.hygiene_ratio * budget.

    Called from run_turn() after reset_for_turn() and before _run_model_preflight.
    Uses rough token estimate only — no provider-reported count is available pre-turn.
    Sets deps.runtime.history_compaction_applied when compaction runs.
    Fails open: any exception returns message_history unchanged so the turn proceeds.
    """
    try:
        ctx_window = model.context_window if model is not None else None
        budget = resolve_compaction_budget(deps.config, ctx_window)
        if budget <= 0:
            return message_history
        token_count = estimate_message_tokens(message_history)
        if token_count <= int(budget * deps.config.compaction.hygiene_ratio):
            return message_history
        ctx = RunContext(deps=deps, model=model, usage=RunUsage())
        return await summarize_history_window(ctx, message_history)
    except Exception:
        log.warning("Pre-turn hygiene compaction failed — skipping", exc_info=True)
        return message_history


# ---------------------------------------------------------------------------
# build_recall_injection — preflight callable (async — memory recall, no LLM)
# ---------------------------------------------------------------------------


def _get_last_user_message(messages: list[ModelMessage]) -> str | None:
    """Extract the text of the most recent UserPromptPart from messages."""
    for msg in reversed(messages):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return None


def _count_user_turns(messages: list[ModelMessage]) -> int:
    """Count ModelRequest messages that contain a non-system UserPromptPart."""
    count = 0
    for msg in messages:
        if isinstance(msg, ModelRequest) and any(isinstance(p, UserPromptPart) for p in msg.parts):
            count += 1
    return count


async def build_recall_injection(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    current_input: str | None = None,
) -> tuple[ModelRequest, int, bool]:
    """Return (injection_message, user_turn_count, recall_fired) for model-bound preflight.

    Injects date, personality memories, and (conditionally) recalled knowledge.
    ``current_input`` is the pending user message not yet in ``messages`` — used
    to match the turn count the SDK would compute when running history processors.

    Caller updates memory_recall_state when recall_fired is True:
      state.last_recall_user_turn = user_turn_count
      state.recall_count += 1
    """
    state: MemoryRecallState = ctx.deps.session.memory_recall_state
    # Match the turn count the SDK sees (user_input appended before processors)
    user_turn_count = _count_user_turns(messages) + (1 if current_input is not None else 0)
    user_msg = current_input or _get_last_user_message(messages)

    injection_parts: list[SystemPromptPart] = []

    # Current date: on every preflight — can change at midnight
    from datetime import date

    injection_parts.append(SystemPromptPart(content=f"Today is {date.today().isoformat()}."))

    # Personality memories: on every preflight (never in @agent.instructions — cache invalidation)
    if ctx.deps.config.personality:
        from co_cli.prompts.personalities._injector import _load_personality_memories

        personality_content = _load_personality_memories()
        if personality_content:
            injection_parts.append(SystemPromptPart(content=personality_content))

    # Knowledge recall: only on new user turns
    recall_fired = False
    if user_msg and user_turn_count > state.last_recall_user_turn:
        from co_cli.tools.knowledge.read import _recall_for_context

        try:
            result = await _recall_for_context(ctx, user_msg, max_results=3)
            recall_fired = True
            # _recall_for_context always returns a str via tool_output(); cast narrows ToolReturnContent
            if (result.metadata or {}).get("count", 0) > 0:
                memory_content = cast("str", result.return_value)
                max_chars = ctx.deps.config.memory.injection_max_chars
                if len(memory_content) > max_chars:
                    memory_content = memory_content[:max_chars]
                injection_parts.append(
                    SystemPromptPart(content=f"Relevant memories:\n{memory_content}")
                )
        except Exception:
            log.debug("build_recall_injection: _recall_for_context failed", exc_info=True)

    return ModelRequest(parts=injection_parts), user_turn_count, recall_fired


# ---------------------------------------------------------------------------
# build_safety_injection — preflight callable (sync — doom loop + shell reflection cap)
# ---------------------------------------------------------------------------


def _count_consecutive_same_calls(messages: list[ModelMessage]) -> int:
    """Count the most-recent contiguous streak of identical tool calls.

    Scans in reverse; stops when a different call is seen or after 10 calls.
    """
    consecutive_same: int = 0
    last_hash: str | None = None
    for msg in reversed(messages):
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolCallPart):
                continue
            args_str = json.dumps(
                part.args.args_dict()  # type: ignore[union-attr]  # part.args is str|dict|None at type level; hasattr guard is correct at runtime
                if hasattr(part.args, "args_dict")
                else str(part.args),
                sort_keys=True,
            )
            h = hashlib.md5(f"{part.tool_name}:{args_str}".encode()).hexdigest()
            if last_hash is None:
                consecutive_same = 1
                last_hash = h
            elif h == last_hash:
                consecutive_same += 1
            else:
                return consecutive_same
    return consecutive_same


def _is_shell_error_return(part: ToolReturnPart) -> bool:
    """Return True when the tool return represents a shell command error."""
    content = part.content
    if isinstance(content, str):
        c = content.lower()
        # Require "error" at the start or the pydantic-ai ModelRetry prefix.
        # Substring match on the whole output caused false positives on text like
        # "3 tests passed, 0 errors".
        str_is_error = (
            c.startswith("error")
            or c.startswith("shell: command failed")
            or c.startswith("shell: unexpected error")
        )
    else:
        str_is_error = False
    return (isinstance(part.metadata, dict) and bool(part.metadata.get("error"))) or (
        isinstance(content, str) and part.tool_name == "shell" and str_is_error
    )


def _count_consecutive_shell_errors(messages: list[ModelMessage]) -> int:
    """Count the most-recent contiguous streak of shell command errors.

    Scans in reverse; stops at the first non-error return.
    """
    count: int = 0
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if _is_shell_error_return(part) and part.tool_name == "shell":
                count += 1
            else:
                return count
    return count


def build_safety_injection(
    deps: CoDeps,
    messages: list[ModelMessage],
) -> tuple[list[ModelRequest], bool, bool]:
    """Return (injections, doom_flagged, reflection_flagged) for model-bound preflight.

    Scans for doom loops and shell error streaks. Returns empty list when no issues
    detected or when the relevant flag is already set.

    Caller updates safety_state flags when the corresponding bool is True:
      if doom_flagged: state.doom_loop_injected = True
      if reflection_flagged: state.reflection_injected = True
    """
    state: SafetyState | None = deps.runtime.safety_state
    if state is None or (state.doom_loop_injected and state.reflection_injected):
        return [], False, False

    doom_threshold = deps.config.doom_loop_threshold
    max_refl = deps.config.max_reflections

    consecutive_same = _count_consecutive_same_calls(messages)
    consecutive_shell_errors = _count_consecutive_shell_errors(messages)

    injections: list[ModelRequest] = []
    doom_flagged = False
    reflection_flagged = False

    if not state.doom_loop_injected and consecutive_same >= doom_threshold:
        injections.append(
            ModelRequest(
                parts=[
                    SystemPromptPart(
                        content=(
                            "You are repeating the same tool call. "
                            "Try a different approach or explain why you are stuck."
                        ),
                    ),
                ]
            )
        )
        doom_flagged = True
        log.warning("Doom loop detected: %d identical tool calls", consecutive_same)

    if not state.reflection_injected and consecutive_shell_errors >= max_refl:
        injections.append(
            ModelRequest(
                parts=[
                    SystemPromptPart(
                        content=(
                            "Shell reflection limit reached. Ask the user for help "
                            "or try a fundamentally different approach."
                        ),
                    ),
                ]
            )
        )
        reflection_flagged = True
        log.warning("Shell reflection cap: %d consecutive errors", consecutive_shell_errors)

    return injections, doom_flagged, reflection_flagged
