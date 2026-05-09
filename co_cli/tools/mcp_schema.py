"""MCP tool inputSchema sanitizer — normalizes malformed schemas for Ollama/Gemini backends."""

from __future__ import annotations

import copy
from typing import Any


def sanitize_mcp_schema(schema: Any) -> dict:
    """Return a sanitized deep copy of an MCP tool inputSchema.

    Always returns a top-level object schema with a `properties` dict.
    Never mutates the input.
    """
    result = _sanitize_node(copy.deepcopy(schema))
    if "type" not in result:
        result["type"] = "object"
    if result.get("type") == "object" and "properties" not in result:
        result["properties"] = {}
    return result


def _sanitize_node(node: Any) -> dict:
    if not isinstance(node, dict):
        return {"type": "object", "properties": {}}
    _fix_type_array(node)
    _collapse_nullable_union(node)
    _fix_object_shape(node)
    _recurse_children(node)
    return node


def _fix_type_array(node: dict) -> None:
    """Collapse type arrays to a single scalar, dropping null."""
    if isinstance(node.get("type"), list):
        types = [t for t in node["type"] if t != "null"]
        node["type"] = types[0] if types else "object"
    # Infer missing type from structural hints
    if "type" not in node and ("properties" in node or "required" in node):
        node["type"] = "object"


def _collapse_nullable_union(node: dict) -> None:
    """Collapse anyOf/oneOf with a single non-null branch into the node."""
    for key in ("anyOf", "oneOf"):
        if key not in node:
            continue
        branches = node[key]
        non_null = [b for b in branches if b.get("type") != "null"]
        if len(non_null) != 1:
            continue
        branch = non_null[0]
        for meta in ("description", "title", "default", "examples"):
            if meta in node and meta not in branch:
                branch[meta] = node[meta]
        node.clear()
        node.update(branch)
        break


def _fix_object_shape(node: dict) -> None:
    """Inject missing properties dict and prune invalid required entries."""
    if node.get("type") == "object" and "properties" not in node:
        node["properties"] = {}
    if "required" in node and "properties" in node:
        valid = set(node["properties"])
        node["required"] = [r for r in node["required"] if r in valid]
        if not node["required"]:
            del node["required"]


def _recurse_children(node: dict) -> None:
    """Recursively sanitize all child schema nodes."""
    if isinstance(node.get("properties"), dict):
        node["properties"] = {k: _sanitize_node(v) for k, v in node["properties"].items()}
    if "items" in node:
        node["items"] = _sanitize_node(node["items"])
    if isinstance(node.get("additionalProperties"), dict):
        node["additionalProperties"] = _sanitize_node(node["additionalProperties"])
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(node.get(key), list):
            node[key] = [_sanitize_node(b) for b in node[key]]
    for key in ("$defs", "definitions"):
        if isinstance(node.get(key), dict):
            node[key] = {k: _sanitize_node(v) for k, v in node[key].items()}
