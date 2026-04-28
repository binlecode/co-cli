"""Unified knowledge artifact data model.

KnowledgeArtifact is the single reusable-artifact model — preferences,
decisions, rules, feedback, articles, references, and notes share this schema.
Memory (raw transcripts) lives in ``sessions/``; knowledge (everything reusable)
lives in ``knowledge_dir/``. See ``docs/specs/memory-knowledge.md`` for the
combined memory/knowledge model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from co_cli.knowledge.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


class ArtifactKindEnum(StrEnum):
    PREFERENCE = "preference"
    DECISION = "decision"
    RULE = "rule"
    FEEDBACK = "feedback"
    ARTICLE = "article"
    REFERENCE = "reference"
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
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    source_type: str | None = None
    source_ref: str | None = None
    certainty: str | None = None
    decay_protected: bool = False
    last_recalled: str | None = None
    recall_count: int = 0


def _coerce_fields(fm: dict[str, Any], body: str, path: Path) -> KnowledgeArtifact:
    """Build a KnowledgeArtifact from canonical kind=knowledge frontmatter."""
    return KnowledgeArtifact(
        id=str(fm["id"]),
        path=path,
        artifact_kind=fm.get("artifact_kind", ArtifactKindEnum.NOTE.value),
        title=fm.get("title"),
        content=body.strip(),
        created=fm["created"],
        updated=fm.get("updated"),
        description=fm.get("description"),
        tags=list(fm.get("tags") or []),
        related=list(fm.get("related") or []),
        source_type=fm.get("source_type"),
        source_ref=fm.get("source_ref"),
        certainty=fm.get("certainty"),
        decay_protected=bool(fm.get("decay_protected", False)),
        last_recalled=fm.get("last_recalled"),
        recall_count=int(fm.get("recall_count", 0) or 0),
    )


def load_knowledge_artifact(path: Path) -> KnowledgeArtifact:
    """Load a single .md file as a KnowledgeArtifact.

    Requires ``kind: knowledge`` frontmatter with ``id`` and ``created`` set.
    Raises ValueError on any missing required field or unexpected ``kind``.
    """
    raw = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(raw)
    if "id" not in fm:
        raise ValueError(f"{path}: missing required field 'id'")
    if "created" not in fm:
        raise ValueError(f"{path}: missing required field 'created'")
    if fm.get("kind") != "knowledge":
        raise ValueError(f"{path}: expected kind='knowledge', got {fm.get('kind')!r}")
    return _coerce_fields(fm, body, path)


def load_knowledge_artifacts(
    knowledge_dir: Path,
    *,
    artifact_kind: str | None = None,
    tags: list[str] | None = None,
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
        if artifact_kind is not None and artifact.artifact_kind != artifact_kind:
            continue
        if tags is not None and not any(tag in artifact.tags for tag in tags):
            continue
        artifacts.append(artifact)
    return artifacts
