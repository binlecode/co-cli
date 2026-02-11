"""YAML frontmatter parsing and validation utilities.

This module provides functions for parsing and validating YAML frontmatter
in markdown files used by the internal knowledge system.
"""

import re
from typing import Any

import yaml


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
        return {}, content

    try:
        frontmatter = yaml.safe_load(yaml_content)
        if frontmatter is None:
            frontmatter = {}
        if not isinstance(frontmatter, dict):
            # Invalid frontmatter structure, treat as no frontmatter
            return {}, content

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


def validate_context_frontmatter(fm: dict[str, Any]) -> None:
    """Validate context.md frontmatter structure.

    Required fields:
        - version: int
        - updated: ISO8601 timestamp string

    Args:
        fm: Frontmatter dictionary

    Raises:
        ValueError: If frontmatter is invalid
    """
    if "version" not in fm:
        raise ValueError("context.md frontmatter missing required field: version")
    if not isinstance(fm["version"], int):
        raise ValueError("context.md frontmatter field 'version' must be an integer")

    if "updated" not in fm:
        raise ValueError("context.md frontmatter missing required field: updated")
    if not isinstance(fm["updated"], str):
        raise ValueError("context.md frontmatter field 'updated' must be a string")
    # Basic ISO8601 format check (not exhaustive)
    if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", fm["updated"]):
        raise ValueError(
            "context.md frontmatter field 'updated' must be ISO8601 format (YYYY-MM-DDTHH:MM:SS)"
        )


def validate_memory_frontmatter(fm: dict[str, Any]) -> None:
    """Validate memory file frontmatter structure.

    Required fields:
        - id: int
        - created: ISO8601 timestamp string

    Optional fields:
        - tags: list[str]
        - source: str (detected | user-told)
        - auto_category: str (preference | correction | decision | context | pattern)

    Args:
        fm: Frontmatter dictionary

    Raises:
        ValueError: If frontmatter is invalid
    """
    if "id" not in fm:
        raise ValueError("memory frontmatter missing required field: id")
    if not isinstance(fm["id"], int):
        raise ValueError("memory frontmatter field 'id' must be an integer")

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
    if "tags" in fm:
        if not isinstance(fm["tags"], list):
            raise ValueError("memory frontmatter field 'tags' must be a list")
        if not all(isinstance(tag, str) for tag in fm["tags"]):
            raise ValueError("memory frontmatter field 'tags' must contain only strings")

    if "source" in fm:
        if fm["source"] is not None and not isinstance(fm["source"], str):
            raise ValueError("memory frontmatter field 'source' must be a string or null")

    if "auto_category" in fm:
        if fm["auto_category"] is not None and not isinstance(fm["auto_category"], str):
            raise ValueError("memory frontmatter field 'auto_category' must be a string or null")
