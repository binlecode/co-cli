"""YAML frontmatter parsing and validation utilities.

This module provides functions for parsing and validating YAML frontmatter
in markdown files used by the internal knowledge system.
"""

import logging
import re
from enum import StrEnum
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ArtifactTypeEnum(StrEnum):
    SESSION_SUMMARY = "session_summary"


class MemoryKindEnum(StrEnum):
    MEMORY = "memory"
    ARTICLE = "article"


_VALID_PROVENANCE: frozenset[str] = frozenset(
    {
        "detected",
        "user-told",
        "planted",
        "auto_decay",
        "web-fetch",
        "session",
    }
)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Expects frontmatter delimited by --- lines:
        ---
        key: value
        ---
        Body content here

    Args:
        content: Markdown content potentially containing frontmatter

    Returns:
        Tuple of (frontmatter_dict, body_markdown).
        If no frontmatter found, returns ({}, content).
    """
    # Match pattern: start of string, ---, yaml content, ---, body
    pattern = r"^---\s*\n(.*?)\n---\s*\n?(.*)"
    match = re.match(pattern, content, re.DOTALL)

    if not match:
        return {}, content

    yaml_content = match.group(1).strip()
    body = match.group(2)

    # Handle empty frontmatter
    if not yaml_content:
        return {}, body

    try:
        frontmatter = yaml.safe_load(yaml_content)
        if frontmatter is None:
            frontmatter = {}
        if not isinstance(frontmatter, dict):
            # Invalid frontmatter structure, treat as no frontmatter
            return {}, body

        # Convert datetime objects back to ISO8601 strings with Z suffix
        for key, value in frontmatter.items():
            if hasattr(value, "isoformat"):  # datetime object
                iso_str = value.isoformat()
                # Replace +00:00 with Z for standard UTC format
                iso_str = iso_str.replace("+00:00", "Z")
                frontmatter[key] = iso_str

        return frontmatter, body
    except yaml.YAMLError:
        # Malformed YAML, treat as no frontmatter
        return {}, content


def strip_frontmatter(content: str) -> str:
    """Strip YAML frontmatter from markdown, returning body only.

    Args:
        content: Markdown content potentially containing frontmatter

    Returns:
        Body content with frontmatter removed
    """
    _, body = parse_frontmatter(content)
    return body


def validate_memory_frontmatter(fm: dict[str, Any]) -> None:
    """Validate memory file frontmatter structure.

    Required fields:
        - id: int
        - created: ISO8601 timestamp string

    Optional fields:
        - kind: str ("memory" or "article")
        - origin_url: str or null (source URL for articles)
        - provenance: str (detected | user-told | planted | auto_decay | web-fetch | session)
        - tags: list[str]
        - auto_category: str (preference | correction | decision | context | pattern | character)
        - certainty: str (high | medium | low)
        - updated: ISO8601 timestamp string (added when consolidated)
        - decay_protected: bool (prevent decay if true)

    Args:
        fm: Frontmatter dictionary

    Raises:
        ValueError: If frontmatter is invalid
    """
    if "id" not in fm:
        raise ValueError("memory frontmatter missing required field: id")
    if isinstance(fm["id"], bool) or not isinstance(fm["id"], (int, str)):
        raise ValueError("memory frontmatter field 'id' must be an integer or string")
    if isinstance(fm["id"], str) and not fm["id"].strip():
        raise ValueError("memory frontmatter field 'id' must not be empty")

    if "created" not in fm:
        raise ValueError("memory frontmatter missing required field: created")
    if not isinstance(fm["created"], str):
        raise ValueError("memory frontmatter field 'created' must be a string")
    # Basic ISO8601 format check
    if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", fm["created"]):
        raise ValueError(
            "memory frontmatter field 'created' must be ISO8601 format (YYYY-MM-DDTHH:MM:SS)"
        )

    # Validate optional fields if present
    if "kind" in fm:
        if fm["kind"] not in (MemoryKindEnum.MEMORY, MemoryKindEnum.ARTICLE):
            raise ValueError(
                "memory frontmatter field 'kind' must be 'memory' or 'article'"
            )

    if "origin_url" in fm:
        if fm["origin_url"] is not None and not isinstance(fm["origin_url"], str):
            raise ValueError(
                "memory frontmatter field 'origin_url' must be a string or null"
            )

    if "provenance" in fm:
        if fm["provenance"] is not None:
            if not isinstance(fm["provenance"], str):
                raise ValueError(
                    "memory frontmatter field 'provenance' must be a string or null"
                )
            if fm["provenance"] not in _VALID_PROVENANCE:
                raise ValueError(
                    f"memory frontmatter field 'provenance' must be one of "
                    f"{sorted(_VALID_PROVENANCE)}, got {fm['provenance']!r}"
                )

    if "tags" in fm:
        if not isinstance(fm["tags"], list):
            raise ValueError("memory frontmatter field 'tags' must be a list")
        if not all(isinstance(tag, str) for tag in fm["tags"]):
            raise ValueError(
                "memory frontmatter field 'tags' must contain only strings"
            )

    if "auto_category" in fm:
        if fm["auto_category"] is not None:
            if not isinstance(fm["auto_category"], str):
                raise ValueError(
                    "memory frontmatter field 'auto_category' must be a string or null"
                )
            valid_categories = {
                "preference",
                "correction",
                "decision",
                "context",
                "pattern",
                "character",
            }
            if fm["auto_category"] not in valid_categories:
                logger.warning(
                    "memory frontmatter field 'auto_category' has unknown value %r — ignoring",
                    fm["auto_category"],
                )

    if "certainty" in fm:
        if fm["certainty"] is not None:
            if not isinstance(fm["certainty"], str):
                raise ValueError(
                    "memory frontmatter field 'certainty' must be a string or null"
                )
            valid_certainties = {"high", "medium", "low"}
            if fm["certainty"] not in valid_certainties:
                logger.warning(
                    "memory frontmatter field 'certainty' has unknown value %r — ignoring",
                    fm["certainty"],
                )

    if "updated" in fm:
        if not isinstance(fm["updated"], str):
            raise ValueError("memory frontmatter field 'updated' must be a string")
        # Basic ISO8601 format check
        if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", fm["updated"]):
            raise ValueError(
                "memory frontmatter field 'updated' must be ISO8601 format (YYYY-MM-DDTHH:MM:SS)"
            )

    if "decay_protected" in fm:
        if not isinstance(fm["decay_protected"], bool):
            raise ValueError(
                "memory frontmatter field 'decay_protected' must be a boolean"
            )

    if "title" in fm:
        if fm["title"] is not None and not isinstance(fm["title"], str):
            raise ValueError(
                "memory frontmatter field 'title' must be a string or null"
            )

    if "related" in fm:
        if fm["related"] is not None:
            if not isinstance(fm["related"], list):
                raise ValueError(
                    "memory frontmatter field 'related' must be a list or null"
                )
            if not all(isinstance(s, str) for s in fm["related"]):
                raise ValueError(
                    "memory frontmatter field 'related' must contain only strings"
                )

    if "artifact_type" in fm:
        if fm["artifact_type"] is not None:
            if not isinstance(fm["artifact_type"], str):
                raise ValueError(
                    "memory frontmatter field 'artifact_type' must be a string or null"
                )
            valid_artifact_types = {e.value for e in ArtifactTypeEnum}
            if fm["artifact_type"] not in valid_artifact_types:
                logger.warning(
                    "memory frontmatter field 'artifact_type' has unknown value %r — ignoring",
                    fm["artifact_type"],
                )

    if "always_on" in fm:
        if not isinstance(fm["always_on"], bool):
            raise ValueError("memory frontmatter field 'always_on' must be a boolean")
