"""Behavioral tests for MCP schema sanitizer and _SanitizingMCPServer proxy."""

import asyncio
from types import SimpleNamespace

from co_cli.agent.mcp import _SanitizingMCPServer
from co_cli.tools.mcp_schema import sanitize_mcp_schema

# ---------------------------------------------------------------------------
# Schema-unit tests (pure sanitizer, no I/O)
# ---------------------------------------------------------------------------


def test_bare_string_type_wrapped() -> None:
    # _sanitize_node called with a bare string "object" — non-dict guard fires
    result = sanitize_mcp_schema("object")
    assert result == {"type": "object", "properties": {}}


def test_type_array_collapsed() -> None:
    result = sanitize_mcp_schema({"type": ["string", "null"], "description": "x"})
    assert result["type"] == "string"
    assert result["description"] == "x"
    assert "null" not in str(result.get("type", ""))


def test_anyof_nullable_collapsed() -> None:
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "y"}
    result = sanitize_mcp_schema(schema)
    assert result["type"] == "string"
    assert result["description"] == "y"
    assert "anyOf" not in result


def test_missing_properties_injected() -> None:
    result = sanitize_mcp_schema({"type": "object"})
    assert result["properties"] == {}


def test_missing_type_inferred() -> None:
    schema = {"properties": {"x": {"type": "integer"}}, "required": ["x"]}
    result = sanitize_mcp_schema(schema)
    assert result["type"] == "object"


def test_invalid_required_pruned() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a", "b"],
    }
    result = sanitize_mcp_schema(schema)
    assert result["required"] == ["a"]


def test_null_input() -> None:
    result = sanitize_mcp_schema(None)
    assert result == {"type": "object", "properties": {}}


def test_recursive_nested() -> None:
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": ["integer", "null"]},
        },
    }
    result = sanitize_mcp_schema(schema)
    assert result["properties"]["count"]["type"] == "integer"
    assert result["properties"]["count"].get("type") != ["integer", "null"]


def test_deep_copy_no_mutation() -> None:
    original = {"type": ["string", "null"], "description": "x"}
    original_copy = dict(original)
    sanitize_mcp_schema(original)
    assert original == original_copy


def test_idempotent() -> None:
    schema = {
        "type": ["string", "null"],
        "properties": {"a": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
        "required": ["a", "missing"],
    }
    once = sanitize_mcp_schema(schema)
    twice = sanitize_mcp_schema(once)
    assert once == twice


# ---------------------------------------------------------------------------
# Integration test — proxy wiring, no live MCP server
# ---------------------------------------------------------------------------


class _FakeMCPServer:
    async def list_tools(self) -> list:
        tool = SimpleNamespace()
        tool.name = "test_tool"
        tool.description = "a tool"
        tool.inputSchema = {"type": ["string", "null"]}
        return [tool]


def test_sanitizer_applied_at_list_tools() -> None:
    proxy = _SanitizingMCPServer(_FakeMCPServer())
    tools = asyncio.run(proxy.list_tools())
    assert len(tools) == 1
    assert tools[0].inputSchema == {"type": "string"}
