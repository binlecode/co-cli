"""Personality helpers — private functions for structural personality delivery.

The ``load_personality`` tool has been retired (M1). All personality content
is now delivered structurally via the system prompt. This module provides
the helper that loads personality-context memories for the
``@agent.system_prompt`` function in ``agent.py``.
"""

import logging
from pathlib import Path

from co_cli.tools.memory import _load_memories

logger = logging.getLogger(__name__)


def _load_personality_memories() -> str:
    """Load personality-context tagged memories for system prompt injection.

    Scans ``.co-cli/knowledge/memories/`` for entries tagged with
    ``personality-context``. Returns the top 5 (by recency) formatted as
    a ``## Learned Context`` section, or empty string if none found.

    Called by ``add_personality_memories()`` in ``agent.py`` on every turn.
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    personality_memories = _load_memories(
        memory_dir, tags=["personality-context"]
    )
    if not personality_memories:
        return ""

    personality_memories.sort(
        key=lambda m: m.updated or m.created,
        reverse=True,
    )
    personality_memories = personality_memories[:5]
    lines = ["## Learned Context", ""]
    for m in personality_memories:
        lines.append(f"- {m.content}")
    return "\n".join(lines)
