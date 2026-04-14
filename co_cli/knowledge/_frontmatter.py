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


class MemoryKindEnum(StrEnum):
    MEMORY = "memory"
    ARTICLE = "article"


class MemoryTypeEnum(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


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


def _validate_id_field(fm: dict[str, Any]) -> None:
    if "id" not in fm:
        raise ValueError("memory frontmatter missing required field: id")
    if isinstance(fm["id"], bool) or not isinstance(fm["id"], (int, str)):
        raise ValueError("memory frontmatter field 'id' must be an integer or string")
    if isinstance(fm["id"], str) and not fm["id"].strip():
        raise ValueError("memory frontmatter field 'id' must not be empty")


def _validate_created_field(fm: dict[str, Any]) -> None:
    if "created" not in fm:
        raise ValueError("memory frontmatter missing required field: created")
    if not isinstance(fm["created"], str):
        raise ValueError("memory frontmatter field 'created' must be a string")
    if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", fm["created"]):
        raise ValueError(
            "memory frontmatter field 'created' must be ISO8601 format (YYYY-MM-DDTHH:MM:SS)"
        )


def _validate_kind_fields(fm: dict[str, Any]) -> None:
    if "kind" in fm and fm["kind"] not in (MemoryKindEnum.MEMORY, MemoryKindEnum.ARTICLE):
        raise ValueError("memory frontmatter field 'kind' must be 'memory' or 'article'")
    if (
        "origin_url" in fm
        and fm["origin_url"] is not None
        and not isinstance(fm["origin_url"], str)
    ):
        raise ValueError("memory frontmatter field 'origin_url' must be a string or null")
    if "tags" in fm:
        if not isinstance(fm["tags"], list):
            raise ValueError("memory frontmatter field 'tags' must be a list")
        if not all(isinstance(tag, str) for tag in fm["tags"]):
            raise ValueError("memory frontmatter field 'tags' must contain only strings")


def _validate_type_and_desc(fm: dict[str, Any]) -> None:
    if "type" in fm and fm["type"] is not None:
        if not isinstance(fm["type"], str):
            raise ValueError("memory frontmatter field 'type' must be a string or null")
        valid_types = {e.value for e in MemoryTypeEnum}
        if fm["type"] not in valid_types:
            logger.warning(
                "memory frontmatter field 'type' has unknown value %r — ignoring",
                fm["type"],
            )
    if "description" in fm and fm["description"] is not None:
        if not isinstance(fm["description"], str):
            raise ValueError("memory frontmatter field 'description' must be a string or null")
        if not fm["description"].strip():
            raise ValueError("memory frontmatter field 'description' must not be empty")
        if "\n" in fm["description"]:
            raise ValueError("memory frontmatter field 'description' must not contain newlines")
        if len(fm["description"]) > 200:
            raise ValueError("memory frontmatter field 'description' must be ≤200 characters")


def _validate_temporal_fields(fm: dict[str, Any]) -> None:
    if "updated" in fm:
        if not isinstance(fm["updated"], str):
            raise ValueError("memory frontmatter field 'updated' must be a string")
        if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", fm["updated"]):
            raise ValueError(
                "memory frontmatter field 'updated' must be ISO8601 format (YYYY-MM-DDTHH:MM:SS)"
            )
    if "decay_protected" in fm and not isinstance(fm["decay_protected"], bool):
        raise ValueError("memory frontmatter field 'decay_protected' must be a boolean")
    if "title" in fm and fm["title"] is not None and not isinstance(fm["title"], str):
        raise ValueError("memory frontmatter field 'title' must be a string or null")


def _validate_relationship_fields(fm: dict[str, Any]) -> None:
    if "related" in fm and fm["related"] is not None:
        if not isinstance(fm["related"], list):
            raise ValueError("memory frontmatter field 'related' must be a list or null")
        if not all(isinstance(s, str) for s in fm["related"]):
            raise ValueError("memory frontmatter field 'related' must contain only strings")
    if "always_on" in fm and not isinstance(fm["always_on"], bool):
        raise ValueError("memory frontmatter field 'always_on' must be a boolean")


def render_memory_file(fm: dict[str, Any], body: str) -> str:
    """Render a memory file content string from frontmatter dict and body text."""
    return f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{body.strip()}\n"


def validate_memory_frontmatter(fm: dict[str, Any]) -> None:
    """Validate memory file frontmatter structure.

    Required fields:
        - id: int
        - created: ISO8601 timestamp string

    Optional fields:
        - kind: str ("memory" or "article")
        - origin_url: str or null (source URL for articles)
        - type: str (user | feedback | project | reference)
        - description: str (≤200 chars, no newlines — purpose hook for manifest dedup)
        - tags: list[str]
        - updated: ISO8601 timestamp string (added when consolidated)
        - decay_protected: bool (prevent decay if true)

    Args:
        fm: Frontmatter dictionary

    Raises:
        ValueError: If frontmatter is invalid
    """
    _validate_id_field(fm)
    _validate_created_field(fm)
    _validate_kind_fields(fm)
    _validate_type_and_desc(fm)
    _validate_temporal_fields(fm)
    _validate_relationship_fields(fm)
