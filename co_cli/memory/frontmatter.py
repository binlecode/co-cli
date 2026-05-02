"""YAML frontmatter parse and render for knowledge artifacts.

See ``co_cli.memory.artifact.KnowledgeArtifact`` for the data model.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from co_cli.memory.artifact import KnowledgeArtifact

logger = logging.getLogger(__name__)

KIND_KNOWLEDGE = "knowledge"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter delimited by ``---`` lines. Returns ({}, content) on miss."""
    pattern = r"^---\s*\n(.*?)\n---\s*\n?(.*)"
    match = re.match(pattern, content, re.DOTALL)
    if not match:
        return {}, content

    yaml_content = match.group(1).strip()
    body = match.group(2)
    if not yaml_content:
        return {}, body

    try:
        frontmatter = yaml.safe_load(yaml_content)
        if frontmatter is None:
            frontmatter = {}
        if not isinstance(frontmatter, dict):
            return {}, body

        for key, value in frontmatter.items():
            if hasattr(value, "isoformat"):
                iso_str = value.isoformat().replace("+00:00", "Z")
                frontmatter[key] = iso_str

        return frontmatter, body
    except yaml.YAMLError:
        return {}, content


def strip_frontmatter(content: str) -> str:
    """Return the markdown body with any YAML frontmatter removed."""
    _, body = parse_frontmatter(content)
    return body


def artifact_to_frontmatter(artifact: KnowledgeArtifact) -> dict[str, Any]:
    """Serialize a KnowledgeArtifact to its frontmatter dict.

    Drops None, empty-list, and default-valued fields so files stay readable.
    Always keeps id, kind, artifact_kind, created (required identity).
    """
    frontmatter: dict[str, Any] = {
        "id": artifact.id,
        "kind": KIND_KNOWLEDGE,
        "artifact_kind": artifact.artifact_kind,
        "created": artifact.created,
    }
    optional: list[tuple[str, Any]] = [
        ("title", artifact.title),
        ("description", artifact.description),
        ("updated", artifact.updated),
        ("related", list(artifact.related)),
        ("source_type", artifact.source_type),
        ("source_ref", artifact.source_ref),
        ("last_recalled", artifact.last_recalled),
        ("recall_count", artifact.recall_count),
    ]
    for key, value in optional:
        if value:
            frontmatter[key] = value
    if artifact.decay_protected:
        frontmatter["decay_protected"] = True
    return frontmatter


def render_knowledge_file(artifact: KnowledgeArtifact) -> str:
    """Render a KnowledgeArtifact to a .md file (YAML frontmatter + body)."""
    frontmatter = artifact_to_frontmatter(artifact)
    return render_frontmatter(frontmatter, artifact.content)


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize a frontmatter dict + body to .md text.

    Used by in-place updates that already hold a parsed frontmatter dict.
    For new writes, prefer ``render_knowledge_file(artifact)``.
    """
    yaml_text = yaml.dump(frontmatter, default_flow_style=False, sort_keys=True)
    return f"---\n{yaml_text}---\n\n{body.strip()}\n"
