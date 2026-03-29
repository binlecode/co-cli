"""Shared type definitions for the context package.

Extracted from _history.py to break the circular import between deps.py and
context/_history.py. These dataclasses have no dependency on deps.py.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompactionResult:
    """Pre-computed compaction summary for background processing.

    Produced by ``precompute_compaction()`` during user idle time and
    consumed by ``truncate_history_window()`` on the next turn to skip
    the inline LLM summarization call.

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
