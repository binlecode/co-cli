"""Runtime state for the memory subsystem.

Session-scoped state owned by ``CoSessionState`` and consumed by memory-recall
injection in ``co_cli/context/prompt_text.py``. Kept dependency-free (no
import of ``deps``) so both sides can import it without cycles.
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
