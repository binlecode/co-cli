"""Functional tests for MCP discovery failure recording in bootstrap/core.py."""

import pytest
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.toolsets import DeferredLoadingToolset

from co_cli.agent._mcp import discover_mcp_tools


@pytest.mark.asyncio
async def test_discover_mcp_tools_real_failure_returns_error() -> None:
    """discover_mcp_tools with a non-existent binary yields a non-empty errors dict.

    MCPServerStdio.list_tools() spawns the binary; a missing binary raises FileNotFoundError,
    which discover_mcp_tools catches and records in errors keyed by tool_prefix.
    """
    server = MCPServerStdio(
        "nonexistent-binary-xyz",
        args=[],
        tool_prefix="testprefix",
    )
    toolset = DeferredLoadingToolset(server)
    _, errors, _ = await discover_mcp_tools([toolset], exclude=set())

    assert errors, "errors dict must be non-empty when MCP server binary does not exist"
    assert "testprefix" in errors, (
        "errors must be keyed by tool_prefix when server fails to list tools"
    )
