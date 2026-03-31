"""Shared type definitions for the context package.

Extracted from _history.py to break the circular import between deps.py and
context/_history.py. These dataclasses have no dependency on deps.py.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _CompactionBoundaries:
    """Pre-computed head/tail boundary positions for a compaction pass.

    Produced by ``_compute_compaction_boundaries()`` and consumed by both
    ``truncate_history_window()`` and ``precompute_compaction()``.

    When ``valid`` is ``False``, no clean boundary could be found and the
    caller must skip compaction.
    """

    head_end: int
    tail_start: int
    dropped_count: int
    valid: bool


@dataclass
class CompactionResult:
    """Pre-computed compaction summary for background processing.

    Produced by ``precompute_compaction()`` during user idle time and
    consumed by ``truncate_history_window()`` on the next turn when the
    message boundaries still match.

    The ``message_count`` field is a stale-check: if the message list
    length has changed since computation, the result is discarded.
    """

    summary_text: str
    head_end: int
    tail_start: int
    message_count: int


@dataclass
class MemoryRecallState:
    """Session-scoped state. Tracks memory recall across turns to debounce per-turn recall.

    Owned by CoSessionState.memory_recall_state.
    """

    recall_count: int = 0
    model_request_count: int = 0
    last_recall_user_turn: int = 0


@dataclass
class SafetyState:
    """Turn-scoped state for safety checks.

    Initialized in create_deps() and reset at the start of each turn by run_turn(),
    stored on CoDeps.runtime.safety_state.
    """

    doom_loop_injected: bool = False
    reflection_injected: bool = False
