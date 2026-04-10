"""Memory data access — loading and filtering memory files.

Extracted from tools/memory.py to break the context/ → tools/ cycle.
tools/memory.py re-imports these symbols so tool functions can use them directly.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from co_cli.knowledge._frontmatter import (
    parse_frontmatter,
    validate_memory_frontmatter,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """In-memory representation of a loaded memory file."""

    id: int | str
    path: Path
    content: str
    tags: list[str]
    created: str  # ISO8601
    updated: str | None = None
    related: list[str] | None = None
    kind: str = "memory"
    artifact_type: str | None = None
    always_on: bool = False
    description: str | None = None
    type: str | None = None


# ---------------------------------------------------------------------------
# Single file scanner — supports optional tag filtering at parse time
# ---------------------------------------------------------------------------


def load_memories(
    memory_dir: Path,
    *,
    tags: list[str] | None = None,
    kind: str | None = None,
) -> list[MemoryEntry]:
    """Load and validate memory files from a directory.

    When *tags* is provided, only entries whose tags intersect with the
    requested tags are returned (filtering at parse time). When *tags* is
    None, all entries are loaded.

    When *kind* is provided, only entries of that kind are returned.
    Files without a kind field default to "memory".

    Args:
        memory_dir: Path to the knowledge directory
        tags: Optional tag filter — only entries matching at least one tag
              are included. None means load all.
        kind: Optional kind filter — "memory" or "article". None means load all.

    Returns:
        List of validated MemoryEntry objects
    """
    if not memory_dir.exists():
        return []

    entries: list[MemoryEntry] = []
    for path in memory_dir.glob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)
            validate_memory_frontmatter(fm)

            # Early exit: skip entries that don't match requested kind
            entry_kind = fm.get("kind", "memory")
            if kind is not None and entry_kind != kind:
                continue

            # Early exit: skip entries that don't match requested tags
            if tags is not None:
                entry_tags = fm.get("tags", [])
                if not any(t in entry_tags for t in tags):
                    continue

            entries.append(
                MemoryEntry(
                    id=fm["id"],
                    path=path,
                    content=body.strip(),
                    tags=fm.get("tags", []),
                    created=fm["created"],
                    updated=fm.get("updated"),
                    related=fm.get("related"),
                    kind=entry_kind,
                    artifact_type=fm.get("artifact_type"),
                    always_on=fm.get("always_on", False),
                    description=fm.get("description"),
                    type=fm.get("type"),
                )
            )
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
            continue
    return entries


# ---------------------------------------------------------------------------
# Always-on standing context
# ---------------------------------------------------------------------------


_ALWAYS_ON_CAP = 5


def load_always_on_memories(memory_dir: "Path") -> list[MemoryEntry]:
    """Return up to _ALWAYS_ON_CAP memories with always_on=True.

    Loads all memories, filters to always_on entries, and caps at 5.
    Returns an empty list when memory_dir does not exist.
    """
    if not memory_dir.exists():
        return []
    memories = load_memories(memory_dir)
    return [m for m in memories if m.always_on][:_ALWAYS_ON_CAP]
