"""YAML frontmatter parse, validate, and render for knowledge artifacts.

See ``co_cli.knowledge._artifact.KnowledgeArtifact`` for the data model.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from co_cli.knowledge._artifact import KnowledgeArtifact

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


def _require_iso8601(fm: dict[str, Any], key: str, required: bool) -> None:
    value = fm.get(key)
    if value is None:
        if required:
            raise ValueError(f"knowledge frontmatter missing required field: {key}")
        return
    if not isinstance(value, str) or not _ISO8601_RE.match(value):
        raise ValueError(
            f"knowledge frontmatter field '{key}' must be ISO8601 (YYYY-MM-DDTHH:MM:SS)"
        )


def _require_str_list(fm: dict[str, Any], key: str) -> None:
    value = fm.get(key)
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"knowledge frontmatter field '{key}' must be a list of strings")


def _validate_identity(fm: dict[str, Any]) -> None:
    if "id" not in fm:
        raise ValueError("knowledge frontmatter missing required field: id")
    if isinstance(fm["id"], bool) or not isinstance(fm["id"], (int, str)):
        raise ValueError("knowledge frontmatter field 'id' must be an integer or string")
    if isinstance(fm["id"], str) and not fm["id"].strip():
        raise ValueError("knowledge frontmatter field 'id' must not be empty")
    if fm.get("kind") != KIND_KNOWLEDGE:
        raise ValueError(
            f"knowledge frontmatter field 'kind' must be {KIND_KNOWLEDGE!r} "
            f"(got {fm.get('kind')!r})"
        )
    if "artifact_kind" not in fm or not isinstance(fm["artifact_kind"], str):
        raise ValueError("knowledge frontmatter missing required field: artifact_kind")


def _validate_string_fields(fm: dict[str, Any]) -> None:
    for key in ("title", "description", "source_type", "source_ref", "certainty", "pin_mode"):
        if key in fm and fm[key] is not None and not isinstance(fm[key], str):
            raise ValueError(f"knowledge frontmatter field {key!r} must be a string or null")
    description = fm.get("description")
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


def _validate_typed_scalars(fm: dict[str, Any]) -> None:
    if "decay_protected" in fm and not isinstance(fm["decay_protected"], bool):
        raise ValueError("knowledge frontmatter field 'decay_protected' must be a boolean")
    if "recall_count" in fm and not isinstance(fm["recall_count"], int):
        raise ValueError("knowledge frontmatter field 'recall_count' must be an integer")


def validate_knowledge_frontmatter(fm: dict[str, Any]) -> None:
    """Validate canonical kind=knowledge frontmatter.

    Required: id, kind=knowledge, artifact_kind, created.
    Optional: title, description, updated, tags, related, source_type,
              source_ref, certainty, pin_mode, decay_protected, last_recalled,
              recall_count.
    """
    _validate_identity(fm)
    _require_iso8601(fm, "created", required=True)
    _require_iso8601(fm, "updated", required=False)
    _require_iso8601(fm, "last_recalled", required=False)
    _require_str_list(fm, "tags")
    _require_str_list(fm, "related")
    _validate_string_fields(fm)
    _validate_typed_scalars(fm)


def _artifact_to_frontmatter(artifact: KnowledgeArtifact) -> dict[str, Any]:
    """Serialize a KnowledgeArtifact to its frontmatter dict.

    Drops None, empty-list, and default-valued fields so files stay readable.
    Always keeps id, kind, artifact_kind, created (required identity).
    """
    fm: dict[str, Any] = {
        "id": artifact.id,
        "kind": KIND_KNOWLEDGE,
        "artifact_kind": artifact.artifact_kind,
        "created": artifact.created,
    }
    optional: list[tuple[str, Any]] = [
        ("title", artifact.title),
        ("description", artifact.description),
        ("updated", artifact.updated),
        ("tags", list(artifact.tags)),
        ("related", list(artifact.related)),
        ("source_type", artifact.source_type),
        ("source_ref", artifact.source_ref),
        ("certainty", artifact.certainty),
        ("last_recalled", artifact.last_recalled),
        ("recall_count", artifact.recall_count),
    ]
    for key, value in optional:
        if value:
            fm[key] = value
    if artifact.pin_mode and artifact.pin_mode != "none":
        fm["pin_mode"] = artifact.pin_mode
    if artifact.decay_protected:
        fm["decay_protected"] = True
    return fm


def render_knowledge_file(artifact: KnowledgeArtifact) -> str:
    """Render a KnowledgeArtifact to a .md file (YAML frontmatter + body)."""
    fm = _artifact_to_frontmatter(artifact)
    return render_frontmatter(fm, artifact.content)


def render_frontmatter(fm: dict[str, Any], body: str) -> str:
    """Serialize a frontmatter dict + body to .md text.

    Used by in-place updates (``update_memory``, ``append_memory``) that already
    hold a parsed frontmatter dict. For new writes, prefer
    ``render_knowledge_file(artifact)``.
    """
    yaml_text = yaml.dump(fm, default_flow_style=False, sort_keys=True)
    return f"---\n{yaml_text}---\n\n{body.strip()}\n"
