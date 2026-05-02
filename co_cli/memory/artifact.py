"""Unified knowledge artifact data model.

KnowledgeArtifact is the single reusable-artifact model — user, rule, article,
and note artifacts share this schema. Memory (raw transcripts) lives in
``sessions/``; knowledge (everything reusable) lives in ``knowledge_dir/``.
See ``docs/specs/memory-knowledge.md`` for the knowledge model and
``docs/specs/memory-session.md`` for session recall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


class SourceTypeEnum(StrEnum):
    DETECTED = "detected"
    WEB_FETCH = "web_fetch"
    MANUAL = "manual"
    OBSIDIAN = "obsidian"
    DRIVE = "drive"
    CONSOLIDATED = "consolidated"


class IndexSourceEnum(StrEnum):
    KNOWLEDGE = "knowledge"
    OBSIDIAN = "obsidian"
    DRIVE = "drive"


class CertaintyEnum(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class KnowledgeArtifact:
    """A single reusable knowledge artifact (preferences, rules, articles, notes, …)."""

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
    certainty: str | None = None
    decay_protected: bool = False
    last_recalled: str | None = None
    recall_count: int = 0


def _coerce_fields(frontmatter: dict[str, Any], body: str, path: Path) -> KnowledgeArtifact:
    """Build a KnowledgeArtifact from canonical kind=knowledge frontmatter."""
    return KnowledgeArtifact(
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
        certainty=frontmatter.get("certainty"),
        decay_protected=bool(frontmatter.get("decay_protected", False)),
        last_recalled=frontmatter.get("last_recalled"),
        recall_count=int(frontmatter.get("recall_count", 0) or 0),
    )


def load_knowledge_artifact(path: Path) -> KnowledgeArtifact:
    """Load a single .md file as a KnowledgeArtifact.

    Requires ``kind: knowledge`` frontmatter with ``id`` and ``created`` set.
    Raises ValueError on any missing required field or unexpected ``kind``.
    """
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)
    if "id" not in frontmatter:
        raise ValueError(f"{path}: missing required field 'id'")
    if "created" not in frontmatter:
        raise ValueError(f"{path}: missing required field 'created'")
    if frontmatter.get("kind") != "knowledge":
        raise ValueError(f"{path}: expected kind='knowledge', got {frontmatter.get('kind')!r}")
    return _coerce_fields(frontmatter, body, path)


def load_knowledge_artifacts(
    knowledge_dir: Path,
    *,
    artifact_kinds: list[str] | None = None,
) -> list[KnowledgeArtifact]:
    """Load all .md files under knowledge_dir as KnowledgeArtifacts.

    Files that fail to parse are skipped with a warning. Top-level glob only —
    subdirectories like ``_archive/`` are not traversed.
    """
    if not knowledge_dir.exists():
        return []

    artifacts: list[KnowledgeArtifact] = []
    for path in knowledge_dir.glob("*.md"):
        try:
            artifact = load_knowledge_artifact(path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
            continue
        if artifact_kinds is not None and artifact.artifact_kind not in artifact_kinds:
            continue
        artifacts.append(artifact)
    return artifacts
