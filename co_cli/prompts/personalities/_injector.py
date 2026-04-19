"""Personality helpers — per-turn memory injection.

All task-type mindset files are loaded statically into the soul block at agent
creation via ``load_soul_mindsets()`` in ``personalities/_loader.py``. This module handles
only the per-turn personality-context memory injection.
"""

from co_cli.config._core import KNOWLEDGE_DIR
from co_cli.knowledge._artifact import load_knowledge_artifacts

_personality_cache: str | None = None


def invalidate_personality_cache() -> None:
    """Call after any tool write that may add or remove the personality-context tag.

    The cache is process-scoped. It is safe because personality-context artifacts
    are curated offline and no production tool currently writes this tag at runtime.
    If a future tool gains that capability, it must call this function after writing.
    """
    global _personality_cache
    _personality_cache = None


def _load_personality_memories() -> str:
    """Load personality-context tagged artifacts for system prompt injection.

    Scans ``~/.co-cli/knowledge/`` for entries tagged with
    ``personality-context``. Returns the top 5 (by recency) formatted as
    a ``## Learned Context`` section, or empty string if none found.

    Called by ``build_recall_injection()`` in ``context/_history.py`` on every model-bound preflight.
    Result is cached for the lifetime of the process; call
    ``invalidate_personality_cache()`` if a write tool adds or removes this tag.
    """
    global _personality_cache
    if _personality_cache is not None:
        return _personality_cache

    personality_memories = load_knowledge_artifacts(KNOWLEDGE_DIR, tags=["personality-context"])
    if not personality_memories:
        result = ""
    else:
        personality_memories.sort(
            key=lambda m: m.updated or m.created,
            reverse=True,
        )
        personality_memories = personality_memories[:5]
        lines = ["## Learned Context", ""]
        for m in personality_memories:
            lines.append(f"- {m.content}")
        result = "\n".join(lines)

    _personality_cache = result
    return result
