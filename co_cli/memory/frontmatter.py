"""YAML frontmatter parse and render for memory items.

See ``co_cli.memory.item.MemoryItem`` for the data model.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from co_cli.memory.item import MemoryItem

logger = logging.getLogger(__name__)

KIND_MEMORY = "memory"


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


def memory_item_to_frontmatter(item: MemoryItem) -> dict[str, Any]:
    """Serialize a MemoryItem to its frontmatter dict.

    Drops None, empty-list, and default-valued fields so files stay readable.
    Always keeps id, kind, memory_kind, created (required identity).
    """
    frontmatter: dict[str, Any] = {
        "id": item.id,
        "kind": KIND_MEMORY,
        "memory_kind": item.memory_kind,
        "created": item.created,
    }
    optional: list[tuple[str, Any]] = [
        ("title", item.title),
        ("description", item.description),
        ("updated", item.updated),
        ("related", list(item.related)),
        ("source_type", item.source_type),
        ("source_ref", item.source_ref),
        ("last_recalled", item.last_recalled),
        ("recall_count", item.recall_count),
    ]
    for key, value in optional:
        if value:
            frontmatter[key] = value
    if item.decay_protected:
        frontmatter["decay_protected"] = True
    return frontmatter


def render_memory_item_file(item: MemoryItem) -> str:
    """Render a MemoryItem to a .md file (YAML frontmatter + body)."""
    frontmatter = memory_item_to_frontmatter(item)
    return render_frontmatter(frontmatter, item.content)


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize a frontmatter dict + body to .md text."""
    yaml_text = yaml.dump(frontmatter, default_flow_style=False, sort_keys=True)
    return f"---\n{yaml_text}---\n\n{body.strip()}\n"
