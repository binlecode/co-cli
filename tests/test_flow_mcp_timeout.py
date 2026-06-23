"""Behavioral tests for MCP per-server timeout plumbing.

connect_timeout_seconds bounds connection/discovery (pydantic-ai ``timeout``);
call_timeout_seconds bounds a single tool-call's response (pydantic-ai ``read_timeout``).
The fix's contract is that both configured values reach the constructed MCP server —
the per-call bound was previously never set, leaving the SDK's silent 300s default.
"""

import pytest
from pydantic import ValidationError
from tests._settings import make_settings

from co_cli.agent.core import build_mcp_entries
from co_cli.config.mcp import MCPServerSettings


def test_configured_timeouts_reach_constructed_server() -> None:
    """A stdio server's connect/call timeouts propagate to the pydantic-ai server."""
    config = make_settings(
        mcp_servers={
            "probe": MCPServerSettings(
                command="true",
                connect_timeout_seconds=7,
                call_timeout_seconds=200,
            )
        }
    )

    entries = build_mcp_entries(config, tool_catalog={})

    entry = next(e for e in entries if e.prefix == "probe")
    assert entry.server.timeout == 7
    assert entry.server.read_timeout == 200


def test_call_timeout_defaults_to_stall_window() -> None:
    """Unset call_timeout_seconds defaults to the 120s model-progress stall window."""
    settings = MCPServerSettings(command="true")

    assert settings.call_timeout_seconds == 120


def test_call_timeout_out_of_range_rejected() -> None:
    """call_timeout_seconds above the 600s cap fails validation."""
    with pytest.raises(ValidationError):
        MCPServerSettings(command="true", call_timeout_seconds=601)
