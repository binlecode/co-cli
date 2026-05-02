"""YAML frontmatter parse, validate, and render for knowledge artifacts.

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

_ISO8601_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_DESCRIPTION_MAX = 200


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


def _require_iso8601(frontmatter: dict[str, Any], key: str, required: bool) -> None:
    value = frontmatter.get(key)
    if value is None:
        if required:
            raise ValueError(f"knowledge frontmatter missing required field: {key}")
        return
    if not isinstance(value, str) or not _ISO8601_RE.match(value):
        raise ValueError(
            f"knowledge frontmatter field '{key}' must be ISO8601 (YYYY-MM-DDTHH:MM:SS)"
        )


def _require_str_list(frontmatter: dict[str, Any], key: str) -> None:
    value = frontmatter.get(key)
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"knowledge frontmatter field '{key}' must be a list of strings")


def _validate_identity(frontmatter: dict[str, Any]) -> None:
    if "id" not in frontmatter:
        raise ValueError("knowledge frontmatter missing required field: id")
    if isinstance(frontmatter["id"], bool) or not isinstance(frontmatter["id"], (int, str)):
        raise ValueError("knowledge frontmatter field 'id' must be an integer or string")
    if isinstance(frontmatter["id"], str) and not frontmatter["id"].strip():
        raise ValueError("knowledge frontmatter field 'id' must not be empty")
    if frontmatter.get("kind") != KIND_KNOWLEDGE:
        raise ValueError(
            f"knowledge frontmatter field 'kind' must be {KIND_KNOWLEDGE!r} "
            f"(got {frontmatter.get('kind')!r})"
        )
    if "artifact_kind" not in frontmatter or not isinstance(frontmatter["artifact_kind"], str):
        raise ValueError("knowledge frontmatter missing required field: artifact_kind")


def _validate_string_fields(frontmatter: dict[str, Any]) -> None:
    for key in ("title", "description", "source_type", "source_ref", "certainty"):
        if (
            key in frontmatter
            and frontmatter[key] is not None
            and not isinstance(frontmatter[key], str)
        ):
            raise ValueError(f"knowledge frontmatter field {key!r} must be a string or null")
    description = frontmatter.get("description")
    if description is None:
        return
    if not description.strip():
        raise ValueError("knowledge frontmatter field 'description' must not be empty")
    if "\n" in description:
        raise ValueError("knowledge frontmatter field 'description' must not contain newlines")
    if len(description) > _DESCRIPTION_MAX:
        raise ValueError(
            f"knowledge frontmatter field 'description' must be ≤{_DESCRIPTION_MAX} characters"
        )


def _validate_typed_scalars(frontmatter: dict[str, Any]) -> None:
    if "decay_protected" in frontmatter and not isinstance(frontmatter["decay_protected"], bool):
        raise ValueError("knowledge frontmatter field 'decay_protected' must be a boolean")
    if "recall_count" in frontmatter and not isinstance(frontmatter["recall_count"], int):
        raise ValueError("knowledge frontmatter field 'recall_count' must be an integer")


def validate_knowledge_frontmatter(frontmatter: dict[str, Any]) -> None:
    """Validate canonical kind=knowledge frontmatter.

    Required: id, kind=knowledge, artifact_kind, created.
    Optional: title, description, updated, tags, related, source_type,
              source_ref, certainty, decay_protected, last_recalled,
              recall_count.
    """
    _validate_identity(frontmatter)
    _require_iso8601(frontmatter, "created", required=True)
    _require_iso8601(frontmatter, "updated", required=False)
    _require_iso8601(frontmatter, "last_recalled", required=False)
    _require_str_list(frontmatter, "tags")
    _require_str_list(frontmatter, "related")
    _validate_string_fields(frontmatter)
    _validate_typed_scalars(frontmatter)


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
        ("certainty", artifact.certainty),
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
