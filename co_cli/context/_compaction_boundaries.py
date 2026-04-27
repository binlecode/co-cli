"""Turn grouping and compaction boundary planning.

The planner carves messages into head / dropped / tail regions so the
summarizer and overflow-recovery paths share one algorithm. Turn groups
split at ``UserPromptPart`` boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)

from co_cli.context.summarization import estimate_message_tokens


@dataclass
class TurnGroup:
    """A contiguous group of messages forming one user turn.

    Boundary detection: a new group starts at each ``ModelRequest`` containing
    a ``UserPromptPart``.  Messages before the first such boundary form group 0.
    """

    messages: list[ModelMessage]
    start_index: int


CompactionBoundaries = tuple[int, int, int]
"""(head_end, tail_start, dropped_count) — planner callers receive ``| None`` when no valid boundary exists."""


_MIN_RETAINED_TURN_GROUPS: int = 1
"""Minimum number of turn groups the planner must retain in the tail.

Hardcoded correctness invariant — setting it to 0 breaks the planner.
Not user-configurable. The last turn group is retained unconditionally
even when its tokens alone exceed ``tail_fraction * budget``.
"""


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

    for idx, msg in enumerate(messages):
        is_boundary = isinstance(msg, ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in msg.parts
        )
        if is_boundary and current_msgs:
            groups.append(_make_turn_group(current_msgs, current_start))
            current_msgs = []
            current_start = idx
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
    for idx, msg in enumerate(messages):
        if isinstance(msg, ModelResponse) and any(
            isinstance(p, (TextPart, ThinkingPart)) for p in msg.parts
        ):
            return idx
    return 0


def _find_last_turn_start(messages: list[ModelMessage]) -> int | None:
    """Return the index of the last ModelRequest containing a UserPromptPart.

    Returns ``None`` when no such message exists. Callers that need a slice
    boundary under a protect-tail-or-nothing contract should treat both
    ``None`` and ``0`` as "no boundary to protect".
    """
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, ModelRequest) and any(isinstance(p, UserPromptPart) for p in msg.parts):
            return idx
    return None


def plan_compaction_boundaries(
    messages: list[ModelMessage],
    budget: int,
    tail_fraction: float,
) -> CompactionBoundaries | None:
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
      5. Abort when ``tail_start <= head_end`` (head/tail overlap — nothing to drop).

    Active-user anchoring is structurally guaranteed and requires no explicit
    step: ``group_by_turn`` splits at every ``UserPromptPart``, so the last
    group's ``start_index`` equals the latest user message index. The backward
    walk retains that group unconditionally on its first iteration due to
    ``_MIN_RETAINED_TURN_GROUPS=1``, so ``tail_start <= last_user_idx`` always holds.

    Shared between proactive compaction (``proactive_window_processor``) and
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
        group_tokens = estimate_message_tokens(group.messages)
        if (
            len(acc_groups) >= _MIN_RETAINED_TURN_GROUPS
            and acc_tokens + group_tokens > tail_budget
        ):
            break
        acc_groups.insert(0, group)
        acc_tokens += group_tokens

    if not acc_groups:
        return None

    tail_start = acc_groups[0].start_index
    # Last group is always retained by _MIN_RETAINED_TURN_GROUPS=1: the walk
    # adds the final group unconditionally on its first iteration. Since
    # group_by_turn splits at every UserPromptPart, the latest user prompt
    # is structurally guaranteed to land in acc_groups[0] — no anchoring needed.

    if tail_start <= head_end:
        return None
    return (head_end, tail_start, tail_start - head_end)
