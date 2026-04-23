"""History processors and per-turn dynamic instruction functions for context governance.

History processors are chained via ``Agent(history_processors=[...])``. They run
before every model request and return a transformed message list.

Registered processors (pure transformers — no deps mutation, no in-place mutation of ModelMessage objects):
    dedup_tool_results           — sync, collapses identical-content tool returns to back-reference markers
    truncate_tool_results        — sync, content-clears compactable tool results by recency
    enforce_batch_budget         — sync, spills largest non-persisted tool returns when batch aggregate exceeds threshold
    summarize_history_window     — async, summarizes middle messages via inline LLM or static marker

Dynamic instruction functions (registered via agent.instructions() — never appended to message history):
    _recall_prompt_text          — async, returns date + personality + recalled knowledge as plain text
    _safety_prompt_text          — sync, returns doom-loop / shell-reflection warnings as plain text
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli.context._dedup_tool_results import (
    build_dedup_part,
    dedup_key,
    is_dedup_candidate,
)
from co_cli.context._tool_result_markers import semantic_marker
from co_cli.context.summarization import (
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.context.tool_categories import COMPACTABLE_TOOLS, FILE_TOOLS
from co_cli.context.types import MemoryRecallState
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
Not user-configurable. The last turn group is retained unconditionally
even when its tokens alone exceed ``tail_fraction * budget``.
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
                    "[CONTEXT COMPACTION — REFERENCE ONLY] This session is being "
                    "continued from a previous conversation that ran out of context. "
                    "The summary below is a retrospective recap of completed prior "
                    "work — treat it as background reference, NOT as active "
                    "instructions. Do NOT repeat, redo, or re-execute any action "
                    "already described as completed; do NOT re-answer questions that "
                    "the summary records as resolved. Your active task is identified "
                    "in the '## Active Task' / '## Next Step' sections of the "
                    "summary — resume from there and respond only to user messages "
                    "that appear AFTER this summary.\n\n"
                    f"The summary covers the earlier portion ({dropped_count} "
                    f"messages).\n\n{summary_text}\n\n"
                    "Recent messages are preserved verbatim."
                ),
            ),
        ]
    )


def _build_compaction_marker(dropped_count: int, summary_text: str | None) -> ModelRequest:
    """Return a summary marker when summary_text is present, else a static marker."""
    if summary_text is not None:
        return _summary_marker(dropped_count, summary_text)
    return _static_marker(dropped_count)


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
) -> _CompactionBoundaries | None:
    """Plan ``(head_end, tail_start, dropped_count)`` for a compaction pass.

    Algorithm:
      1. ``head_end = find_first_run_end(messages) + 1``
      2. ``groups = group_by_turn(messages)``; abort when
         ``len(groups) < _MIN_RETAINED_TURN_GROUPS + 1``.
      3. Walk groups from the end, accumulating token estimates. Stop BEFORE
         adding a group that would push accumulated tokens over
         ``tail_fraction * budget``, UNLESS fewer than ``_MIN_RETAINED_TURN_GROUPS``
         groups have been accumulated. In that case the group is retained
         regardless.
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
    acc_groups: list[TurnGroup] = []
    acc_tokens = 0
    for group in reversed(groups):
        gt = estimate_message_tokens(group.messages)
        if len(acc_groups) >= _MIN_RETAINED_TURN_GROUPS and acc_tokens + gt > tail_budget:
            break
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
# Processor helpers
# ---------------------------------------------------------------------------

COMPACTABLE_KEEP_RECENT = 5
"""Keep the N most-recent tool returns per compactable tool type; clear older.

Borrowed from ``fork-claude-code/services/compact/timeBasedMCConfig.ts:33``
(``keepRecent: 5``). Not convergent across peers — codex, hermes, and
opencode do not have per-tool recency retention. Not tuned specifically for
co-cli's tool surface; revisit via ``evals/eval_compaction_quality.py`` if a
retention/fidelity tradeoff becomes measurable.
"""

_CLEARED_PLACEHOLDER = "[tool result cleared — older than 5 most recent calls]"
"""Last-resort fallback when ToolReturnPart.content is non-string (multimodal).

Normal path uses ``semantic_marker`` to produce per-tool descriptions that
preserve intent and outcome signal. The static placeholder survives only
for non-string content shapes where a marker cannot be generated.
"""


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


# ---------------------------------------------------------------------------
# Shared scaffolding for per-part tool-return rewriters
# ---------------------------------------------------------------------------


def _iter_tool_returns_reversed(messages: list[ModelMessage]) -> Iterator[ToolReturnPart]:
    """Yield ToolReturnParts from messages, reverse over both messages and parts."""
    for msg in reversed(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for part in reversed(msg.parts):
            if isinstance(part, ToolReturnPart):
                yield part


def _rewrite_tool_returns(
    messages: list[ModelMessage],
    boundary: int,
    *,
    replacement_for: Callable[[ToolReturnPart], ToolReturnPart | None],
) -> list[ModelMessage]:
    """Rewrite ToolReturnParts in ``messages[:boundary]`` per the given policy.

    ``replacement_for(part)`` returns a new ToolReturnPart to substitute, or
    ``None`` for pass-through. Messages at or after ``boundary`` and every
    non-ModelRequest message are copied verbatim. A ModelRequest is only
    rebuilt via ``replace(msg, parts=...)`` when at least one part changed —
    otherwise the original message object is preserved.

    Shared by ``truncate_tool_results`` and ``dedup_tool_results`` so both
    obey the same "boundary-protected, non-mutating" contract by construction.
    """
    result: list[ModelMessage] = []
    for idx, msg in enumerate(messages):
        if idx >= boundary or not isinstance(msg, ModelRequest):
            result.append(msg)
            continue
        new_parts: list = []
        modified = False
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                replacement = replacement_for(part)
                if replacement is not None:
                    new_parts.append(replacement)
                    modified = True
                    continue
            new_parts.append(part)
        result.append(replace(msg, parts=new_parts) if modified else msg)
    return result


# ---------------------------------------------------------------------------
# 0. dedup_tool_results (sync — no I/O)
# ---------------------------------------------------------------------------


def _build_latest_id_by_key(messages: list[ModelMessage]) -> dict[str, str]:
    """For each ``(tool_name, content-hash)`` key, record the ``tool_call_id`` of the latest occurrence.

    Reverse scan over ToolReturnParts eligible for dedup; the first
    observation in reverse order is the latest in forward order. Caller is
    responsible for scoping ``messages`` (typically ``messages[:boundary]``)
    so the tail is excluded from dedup consideration.
    """
    latest: dict[str, str] = {}
    for part in _iter_tool_returns_reversed(messages):
        if not is_dedup_candidate(part):
            continue
        key = dedup_key(part)
        if key not in latest:
            latest[key] = part.tool_call_id
    return latest


def dedup_tool_results(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Collapse repeat returns of the same ``(tool_name, content)`` pair to back-references.

    For each compactable tool return whose content (≥ 200 chars, string)
    duplicates a more recent return of the same tool, replace with a 1-line
    back-reference marker naming the latest ``call_id``. Only the latest
    occurrence of each ``(tool, hash)`` retains full content.

    Protects the last turn via the same ``_find_last_turn_start`` boundary
    M2a uses. Non-string content and non-compactable tools pass through
    unchanged (same safety envelope as ``truncate_tool_results``).

    Registered as the **first** history processor — runs before
    ``truncate_tool_results`` so the kept recent window is already deduped
    before recency-based clearing applies.
    """
    boundary = _find_last_turn_start(messages)
    if boundary == 0:
        return messages

    latest_id_by_key = _build_latest_id_by_key(messages[:boundary])

    def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
        if not is_dedup_candidate(part):
            return None
        latest_id = latest_id_by_key.get(dedup_key(part))
        if latest_id is None or latest_id == part.tool_call_id:
            return None
        return build_dedup_part(part, latest_id)

    return _rewrite_tool_returns(messages, boundary, replacement_for=replacement_for)


# ---------------------------------------------------------------------------
# 1. truncate_tool_results (sync — no I/O)
# ---------------------------------------------------------------------------


def _build_keep_ids(older: list[ModelMessage]) -> set[int]:
    """Reverse scan: collect ids of the COMPACTABLE_KEEP_RECENT most recent parts per tool."""
    keep_ids: set[int] = set()
    seen_counts: dict[str, int] = {}
    for part in _iter_tool_returns_reversed(older):
        if part.tool_name not in COMPACTABLE_TOOLS:
            continue
        count = seen_counts.get(part.tool_name, 0)
        if count < COMPACTABLE_KEEP_RECENT:
            keep_ids.add(id(part))
        seen_counts[part.tool_name] = count + 1
    return keep_ids


def _build_call_id_to_args(messages: list[ModelMessage]) -> dict[str, dict]:
    """Index ``ToolCallPart.tool_call_id`` → args dict across all ModelResponses.

    Forward scan — later calls overwrite earlier ones on id collision, which
    does not happen in practice (ids are unique per call). Args are read via
    ``args_as_dict``; malformed args fall back to an empty dict so a single
    corrupt call cannot break the index for the rest of the conversation.
    """
    result: dict[str, dict] = {}
    for msg in messages:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolCallPart):
                continue
            try:
                args = part.args_as_dict()
            except Exception:
                args = {}
            result[part.tool_call_id] = args or {}
    return result


def _build_cleared_part(
    part: ToolReturnPart,
    call_id_to_args: dict[str, dict],
) -> ToolReturnPart:
    """Construct the replacement ToolReturnPart for an older-than-5 compactable return.

    Non-string content falls back to the static placeholder — markers need a
    readable content string for char/line/outcome heuristics.
    """
    content = part.content
    if not isinstance(content, str):
        replacement = _CLEARED_PLACEHOLDER
    else:
        args = call_id_to_args.get(part.tool_call_id, {})
        replacement = semantic_marker(part.tool_name, args, content)
    return ToolReturnPart(
        tool_name=part.tool_name,
        content=replacement,
        tool_call_id=part.tool_call_id,
    )


def truncate_tool_results(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    """Content-clear compactable tool results older than the 5 most recent per tool type.

    Replacement content is a per-tool semantic marker (see
    ``semantic_marker``) carrying tool name, key args, and a size/outcome
    signal. Falls back to the static ``_CLEARED_PLACEHOLDER`` when the
    ToolReturnPart carries non-string content.

    Protects the last turn (everything from the last UserPromptPart onward).
    Non-compactable tools pass through intact regardless of count.

    Registered after ``dedup_tool_results`` so the kept window has already
    been collapsed for identical repeats before recency-based clearing
    runs. Cheap in-memory work, no LLM call. ``ctx`` is required by
    pydantic-ai's history processor signature but no config fields are
    accessed.
    """
    boundary = _find_last_turn_start(messages)
    if boundary == 0:
        return messages

    keep_ids = _build_keep_ids(messages[:boundary])
    call_id_to_args = _build_call_id_to_args(messages)

    def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
        if part.tool_name not in COMPACTABLE_TOOLS or id(part) in keep_ids:
            return None
        return _build_cleared_part(part, call_id_to_args)

    return _rewrite_tool_returns(messages, boundary, replacement_for=replacement_for)


# ---------------------------------------------------------------------------
# 2. enforce_batch_budget (sync — disk I/O via persist_if_oversized)
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
) -> list[ModelMessage]:
    """Return a new message list with rebuilt parts for indexes in ``replacements``."""
    result: list[ModelMessage] = []
    for msg_idx, msg in enumerate(messages):
        part_replacements = replacements.get(msg_idx)
        if part_replacements is None or not isinstance(msg, ModelRequest):
            result.append(msg)
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
        result.append(replace(msg, parts=new_parts))
    return result


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

    Registered after truncate_tool_results.
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

    return _apply_batch_replacements(messages, replacements)


# ---------------------------------------------------------------------------
# Context enrichment for summarization
# ---------------------------------------------------------------------------

_CONTEXT_MAX_CHARS = 4_000
_SUMMARY_MARKER_PREFIX = "[CONTEXT COMPACTION — REFERENCE ONLY] This session is being continued from a previous conversation that ran out of context."


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
    return result[:_CONTEXT_MAX_CHARS]


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
        enrichment = _gather_compaction_context(ctx, dropped)
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
    summary_text = await _summarize_dropped_messages(ctx, dropped, announce=announce)
    marker = _build_compaction_marker(dropped_count, summary_text)
    ctx.deps.runtime.history_compaction_applied = True
    ctx.deps.runtime.compacted_in_current_turn = True
    result = [
        *messages[:head_end],
        marker,
        *_preserve_search_tool_breadcrumbs(dropped),
        *messages[tail_start:],
    ]
    return result, summary_text


async def recover_overflow_history(
    ctx: RunContext[CoDeps],
    messages: list[ModelMessage],
    *,
    tail_fraction_override: float | None = None,
) -> list[ModelMessage] | None:
    """Recover from provider context overflow via the shared boundary planner.

    Calls ``plan_compaction_boundaries`` with config-sourced tail settings.
    ``tail_fraction_override`` allows a more aggressive retry with a smaller tail fraction.
    The last turn group is always preserved via ``_MIN_RETAINED_TURN_GROUPS=1``.
    Returns None when no compaction boundary exists.
    """
    ctx_window = ctx.deps.model.context_window if ctx.deps.model else None
    budget = resolve_compaction_budget(ctx.deps.config, ctx_window)
    cfg = ctx.deps.config.compaction
    tail_fraction = (
        tail_fraction_override if tail_fraction_override is not None else cfg.tail_fraction
    )
    token_count = max(estimate_message_tokens(messages), latest_response_input_tokens(messages))
    bounds = plan_compaction_boundaries(
        messages,
        budget,
        tail_fraction,
    )
    if bounds is None:
        log.warning(
            "Compaction: overflow recovery boundary planning returned None "
            "(tail group exceeds budget). budget=%d tail_fraction=%.2f token_count=%d — recovery impossible.",
            budget,
            tail_fraction,
            token_count,
        )
        return None

    result, _ = await _apply_compaction(ctx, messages, bounds, announce=False)
    return result


# ---------------------------------------------------------------------------
# 3. summarize_history_window (async — LLM call)
# ---------------------------------------------------------------------------


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

    # Skip stale API-reported count when compaction already ran this turn — the
    # reported value reflects the pre-compaction context and would re-trigger spuriously.
    reported = (
        0 if ctx.deps.runtime.compacted_in_current_turn else latest_response_input_tokens(messages)
    )
    estimate = estimate_message_tokens(messages)
    token_count = max(estimate, reported)
    token_threshold = max(int(budget * cfg.proactive_ratio), cfg.min_context_length_tokens)

    if token_count <= token_threshold:
        return messages

    # Anti-thrashing gate: skip proactive after N consecutive low-yield proactive runs.
    # Does NOT gate overflow recovery or hygiene — only the proactive path.
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

    # Track proactive compaction savings for the anti-thrashing gate.
    tokens_after = estimate_message_tokens(result)
    savings = (token_count - tokens_after) / token_count if token_count > 0 else 0.0
    if savings < cfg.min_proactive_savings:
        ctx.deps.runtime.consecutive_low_yield_proactive_compactions += 1
    else:
        ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0

    return result


# ---------------------------------------------------------------------------
# maybe_run_pre_turn_hygiene — pre-turn maintenance compaction
# ---------------------------------------------------------------------------


async def maybe_run_pre_turn_hygiene(
    deps: CoDeps,
    message_history: list[ModelMessage],
    model: Any,
    reported_input_tokens: int = 0,
) -> list[ModelMessage]:
    """Pre-turn hygiene compaction: compact if token count exceeds cfg.hygiene_ratio * budget.

    ``reported_input_tokens`` must be read from turn_usage before reset_for_turn() clears it.
    Sets deps.runtime.history_compaction_applied when compaction runs.
    Fails open: any exception returns message_history unchanged so the turn proceeds.
    """
    try:
        ctx_window = model.context_window if model is not None else None
        budget = resolve_compaction_budget(deps.config, ctx_window)
        if budget <= 0:
            return message_history
        token_count = max(estimate_message_tokens(message_history), reported_input_tokens)
        if token_count <= int(budget * deps.config.compaction.hygiene_ratio):
            return message_history
        # Clear the anti-thrashing gate so hygiene is never blocked by prior low-yield
        # proactive runs. Orchestrate.py also clears on return — that's a safe no-op.
        deps.runtime.consecutive_low_yield_proactive_compactions = 0
        ctx = RunContext(deps=deps, model=model, usage=RunUsage())
        return await summarize_history_window(ctx, message_history)
    except Exception:
        log.warning("Pre-turn hygiene compaction failed — skipping", exc_info=True)
        return message_history


# ---------------------------------------------------------------------------
# _recall_prompt_text — per-turn dynamic instruction (async — memory recall, no LLM)
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


async def _recall_prompt_text(ctx: RunContext[CoDeps]) -> str:
    """Per-turn dynamic instruction: date, personality memories, and recalled knowledge."""
    state: MemoryRecallState = ctx.deps.session.memory_recall_state
    user_turn_count = _count_user_turns(ctx.messages)
    user_msg = _get_last_user_message(ctx.messages)

    parts: list[str] = []

    parts.append(f"Today is {date.today().isoformat()}.")

    # Personality memories — re-evaluated every turn (file may be updated between turns).
    if ctx.deps.config.personality:
        from co_cli.prompts.personalities._injector import _load_personality_memories

        personality_content = _load_personality_memories()
        if personality_content:
            parts.append(personality_content)

    # Knowledge recall — only on new user turns.
    if user_msg and user_turn_count > state.last_recall_user_turn:
        from co_cli.tools.knowledge.read import _recall_for_context

        try:
            result = await _recall_for_context(ctx, user_msg, max_results=3)
            state.last_recall_user_turn = user_turn_count
            state.recall_count += 1
            if (result.metadata or {}).get("count", 0) > 0:
                memory_content = cast("str", result.return_value)
                max_chars = ctx.deps.config.memory.injection_max_chars
                if len(memory_content) > max_chars:
                    memory_content = memory_content[:max_chars]
                parts.append(f"Relevant memories:\n{memory_content}")
        except Exception:
            log.debug("_recall_prompt_text: _recall_for_context failed", exc_info=True)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# _safety_prompt_text — per-turn dynamic instruction (doom loop + shell reflection cap)
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


def _safety_prompt_text(ctx: RunContext[CoDeps]) -> str:
    """Per-turn dynamic instruction: doom loop and shell reflection warnings. Empty string when no condition is active."""
    deps = ctx.deps
    messages = ctx.messages
    doom_threshold = deps.config.doom_loop_threshold
    max_refl = deps.config.max_reflections

    consecutive_same = _count_consecutive_same_calls(messages)
    consecutive_shell_errors = _count_consecutive_shell_errors(messages)

    warnings: list[str] = []

    if consecutive_same >= doom_threshold:
        warnings.append(
            "You are repeating the same tool call. "
            "Try a different approach or explain why you are stuck."
        )
        log.warning("Doom loop detected: %d identical tool calls", consecutive_same)

    if consecutive_shell_errors >= max_refl:
        warnings.append(
            "Shell reflection limit reached. Ask the user for help "
            "or try a fundamentally different approach."
        )
        log.warning("Shell reflection cap: %d consecutive errors", consecutive_shell_errors)

    return "\n\n".join(warnings)
