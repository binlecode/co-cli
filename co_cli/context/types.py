"""Shared type definitions for the context package.

Extracted from _history.py to break the circular import between deps.py and
context/_history.py. These dataclasses have no dependency on deps.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryRecallState:
    """Session-scoped state. Tracks memory recall across turns to debounce per-turn recall.

    Owned by CoSessionState.memory_recall_state.
    """

    recall_count: int = 0
    last_recall_user_turn: int = 0
