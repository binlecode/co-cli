"""Personality helpers — per-turn memory injection.

All task-type mindset files are loaded statically into the soul block at agent
creation via ``load_soul_mindsets()`` in ``personalities/_loader.py``. This module handles
only the per-turn personality-context memory injection.
"""

from co_cli.config._core import KNOWLEDGE_DIR
from co_cli.knowledge._artifact import load_knowledge_artifacts


def _load_personality_memories() -> str:
    """Load personality-context tagged artifacts for system prompt injection.

    Scans ``~/.co-cli/knowledge/`` for entries tagged with
    ``personality-context``. Returns the top 5 (by recency) formatted as
    a ``## Learned Context`` section, or empty string if none found.

    Called by ``add_personality_memories()`` in ``agent.py`` on every turn.
    """
    personality_memories = load_knowledge_artifacts(KNOWLEDGE_DIR, tags=["personality-context"])
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
