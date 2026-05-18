"""Unified memory artifact data model.

MemoryArtifact is the single reusable-artifact model — user, rule, article,
and note artifacts share this schema. Sessions (raw transcripts) live in the
session/ module; memory artifacts (everything reusable) live in ``memory_dir/``.
See ``docs/specs/memory.md`` for the memory tier model and
``docs/specs/sessions.md`` for session recall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from co_cli.memory.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


class ArtifactKindEnum(StrEnum):
    USER = "user"
    RULE = "rule"
    ARTICLE = "article"
    NOTE = "note"
    CANON = "canon"


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
class MemoryArtifact:
    """A single reusable memory artifact (preferences, rules, articles, notes, …)."""

    id: str
    path: Path
    artifact_kind: str
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


def _coerce_fields(frontmatter: dict[str, Any], body: str, path: Path) -> MemoryArtifact:
    """Build a MemoryArtifact from canonical kind=memory frontmatter."""
    return MemoryArtifact(
        id=str(frontmatter["id"]),
        path=path,
        artifact_kind=frontmatter.get("artifact_kind", ArtifactKindEnum.NOTE.value),
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


def load_artifact(path: Path) -> MemoryArtifact:
    """Load a single .md file as a MemoryArtifact.

    Requires ``kind: memory`` frontmatter with ``id`` and ``created`` set.
    Raises ValueError on any missing required field or unexpected ``kind``.
    """
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)
    if "id" not in frontmatter:
        raise ValueError(f"{path}: missing required field 'id'")
    if "created" not in frontmatter:
        raise ValueError(f"{path}: missing required field 'created'")
    if frontmatter.get("kind") != "memory":
        raise ValueError(f"{path}: expected kind='memory', got {frontmatter.get('kind')!r}")
    return _coerce_fields(frontmatter, body, path)


def load_artifacts(
    memory_dir: Path,
    *,
    artifact_kinds: list[str] | None = None,
) -> list[MemoryArtifact]:
    """Load all .md files under memory_dir as MemoryArtifacts.

    Files that fail to parse are skipped with a warning. Top-level glob only —
    subdirectories like ``_archive/`` are not traversed.
    """
    if not memory_dir.exists():
        return []

    artifacts: list[MemoryArtifact] = []
    for path in memory_dir.glob("*.md"):
        try:
            artifact = load_artifact(path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
            continue
        if artifact_kinds is not None and artifact.artifact_kind not in artifact_kinds:
            continue
        artifacts.append(artifact)
    return artifacts


def filter_artifacts(
    entries: list[MemoryArtifact], filters: dict[str, Any]
) -> list[MemoryArtifact]:
    """Apply older_than_days filter to a loaded artifact list."""
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


def format_artifact_row(m: MemoryArtifact) -> str:
    id_prefix = m.id[:8]
    created = m.created[:10]
    snippet = m.content[:80]
    return f"{id_prefix}  {created}  [{m.artifact_kind}]  {snippet}"
