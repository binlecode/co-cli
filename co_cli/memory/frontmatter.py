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


def split_frontmatter_raw(text: str) -> tuple[str, str]:
    """Return (raw_frontmatter_block_with_delimiters_and_trailing_newline, body).

    Unlike ``parse_frontmatter`` (which parses the YAML into a dict), this is a
    lossless raw split that preserves the exact delimiter block verbatim for
    rewriting. It deliberately uses a strict ``---\\n`` delimiter contract (no
    surrounding whitespace tolerance) — do not "unify" it with
    ``parse_frontmatter``'s ``---\\s*\\n`` regex; the two serve different needs
    and changing this splitter's rules would silently alter rewrite behavior.

    If no frontmatter, returns ("", text).
    """
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    raw = text[: end + len("\n---\n")]
    body = text[end + len("\n---\n") :]
    return raw, body


def strip_frontmatter(content: str) -> str:
    """Return the markdown body with any YAML frontmatter removed."""
    _, body = parse_frontmatter(content)
    return body


def memory_item_to_frontmatter(item: MemoryItem) -> dict[str, Any]:
    """Serialize a MemoryItem to its frontmatter dict.

    Drops None, empty-list, and default-valued fields so files stay readable.
    Always keeps id, memory_kind, created_at (required identity).

    Enum-typed fields (memory_kind, source_type) are coerced to plain strings:
    yaml.dump dispatches on exact type, so a StrEnum member would otherwise
    serialize as a ``!!python/object`` tag that ``yaml.safe_load`` refuses to
    read back, silently orphaning the file.
    """
    frontmatter: dict[str, Any] = {
        "id": item.id,
        "memory_kind": str(item.memory_kind),
        "created_at": item.created_at,
    }
    optional: list[tuple[str, Any]] = [
        ("title", item.title),
        ("description", item.description),
        ("updated_at", item.updated_at),
        ("related", list(item.related)),
        ("source_type", str(item.source_type) if item.source_type else None),
        ("source_ref", item.source_ref),
        ("last_recalled_at", item.last_recalled_at),
        ("recall_count", item.recall_count),
        ("recall_days", item.recall_days),
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
