"""Unified memory item data model.

MemoryItem is the single reusable memory-item model — user, rule, article,
and note items share this schema. Sessions (raw transcripts) live in the
session/ module; memory items (everything reusable) live in ``memory_dir/``.
See ``docs/specs/memory.md`` for the memory tier model and
``docs/specs/sessions.md`` for session recall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from co_cli.memory.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


class MemoryKindEnum(StrEnum):
    USER = "user"
    RULE = "rule"
    ARTICLE = "article"
    NOTE = "note"
    CANON = "canon"


MemoryKind = Literal[
    MemoryKindEnum.USER,
    MemoryKindEnum.RULE,
    MemoryKindEnum.ARTICLE,
    MemoryKindEnum.NOTE,
]


class SourceTypeEnum(StrEnum):
    DETECTED = "detected"
    WEB_FETCH = "web_fetch"
    MANUAL = "manual"
    OBSIDIAN = "obsidian"
    DRIVE = "drive"
    CONSOLIDATED = "consolidated"


class IndexSourceEnum(StrEnum):
    """`source` column values in the index store for write-eligible domains."""

    MEMORY = "memory"
    OBSIDIAN = "obsidian"
    DRIVE = "drive"


@dataclass
class MemoryItem:
    """A single reusable memory item (preferences, rules, articles, notes, …)."""

    id: str
    path: Path
    memory_kind: str
    title: str | None
    content: str
    created: str
    updated: str | None = None
    description: str | None = None
    related: list[str] = field(default_factory=list)
    source_type: str | None = None
    source_ref: str | None = None
    decay_protected: bool = False
    last_recalled: str | None = None
    recall_count: int = 0


def _coerce_fields(frontmatter: dict[str, Any], body: str, path: Path) -> MemoryItem:
    """Build a MemoryItem from canonical memory frontmatter."""
    return MemoryItem(
        id=str(frontmatter["id"]),
        path=path,
        memory_kind=frontmatter.get("memory_kind", MemoryKindEnum.NOTE.value),
        title=frontmatter.get("title"),
        content=body.strip(),
        created=frontmatter["created"],
        updated=frontmatter.get("updated"),
        description=frontmatter.get("description"),
        related=list(frontmatter.get("related") or []),
        source_type=frontmatter.get("source_type"),
        source_ref=frontmatter.get("source_ref"),
        decay_protected=bool(frontmatter.get("decay_protected", False)),
        last_recalled=frontmatter.get("last_recalled"),
        recall_count=int(frontmatter.get("recall_count", 0) or 0),
    )


def load_memory_item(path: Path) -> MemoryItem:
    """Load a single .md file as a MemoryItem.

    Requires ``id`` and ``created`` in frontmatter. Memory items live under
    ``memory_dir/*.md`` and are peer-independent from sessions, so no tier
    discriminator field is needed.
    """
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)
    if "id" not in frontmatter:
        raise ValueError(f"{path}: missing required field 'id'")
    if "created" not in frontmatter:
        raise ValueError(f"{path}: missing required field 'created'")
    return _coerce_fields(frontmatter, body, path)


def load_memory_items(
    memory_dir: Path,
    *,
    memory_kinds: list[str] | None = None,
) -> list[MemoryItem]:
    """Load all .md files under memory_dir as MemoryItems.

    Files that fail to parse are skipped with a warning. Top-level glob only —
    subdirectories like ``_archive/`` are not traversed.
    """
    if not memory_dir.exists():
        return []

    items: list[MemoryItem] = []
    for path in memory_dir.glob("*.md"):
        try:
            item = load_memory_item(path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
            continue
        if memory_kinds is not None and item.memory_kind not in memory_kinds:
            continue
        items.append(item)
    return items


def filter_memory_items(entries: list[MemoryItem], filters: dict[str, Any]) -> list[MemoryItem]:
    """Apply older_than_days filter to a loaded memory item list."""
    result = entries
    if "older_than_days" in filters:
        cutoff_days = filters["older_than_days"]
        now = datetime.now(UTC)
        result = [
            m
            for m in result
            if (now - datetime.fromisoformat(m.created.replace("Z", "+00:00"))).days > cutoff_days
        ]
    return result


def format_memory_item_row(m: MemoryItem) -> str:
    id_prefix = m.id[:8]
    created = m.created[:10]
    snippet = m.content[:80]
    return f"{id_prefix}  {created}  [{m.memory_kind}]  {snippet}"
