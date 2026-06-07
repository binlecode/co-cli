"""Agent construction core — toolset composition helpers."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.combined import CombinedToolset

from co_cli.config.core import Settings
from co_cli.deps import CoDeps, ToolInfo

if TYPE_CHECKING:
    from co_cli.agent.mcp import MCPToolsetEntry


def build_native_toolset(
    config: Settings,
) -> tuple[AbstractToolset[CoDeps], dict[str, ToolInfo]]:
    """Build the unfiltered native toolset and its tool_index.

    Pure config — no IO. Returns the native FunctionToolset and a fresh
    dict copy of the native tool metadata. Caller is responsible for
    combining with MCP toolsets (if any) and applying the approval-resume
    filter via assemble_routing_toolset().
    """
    from co_cli.agent.toolset import _build_native_toolset

    native_toolset, native_index = _build_native_toolset(config)
    return native_toolset, dict(native_index)


def build_mcp_entries(config: Settings, tool_index: dict[str, ToolInfo]) -> list[MCPToolsetEntry]:
    """Build MCP toolset entries wrapped for sequential-flag propagation.

    Not yet connected. Each entry's toolset is wrapped with _SequentialMCPToolset
    so that ToolDefinition.sequential is patched from tool_index[name].is_concurrent_safe
    at step time. tool_index is held by reference — discover_mcp_tools() populates
    MCP entries into it after connection, before the first get_tools() call.
    """
    from co_cli.agent.mcp import _build_mcp_toolsets, _SequentialMCPToolset

    entries = _build_mcp_toolsets(config)
    return [
        replace(entry, toolset=_SequentialMCPToolset(entry.toolset, tool_index))
        for entry in entries
    ]


def assemble_routing_toolset(
    native_toolset: AbstractToolset[CoDeps],
    mcp_toolsets: list[AbstractToolset[CoDeps]],
) -> AbstractToolset[CoDeps]:
    """Combine native + MCP toolsets, apply the visibility filter, wrap the call_tool seam.

    The routing wrapper sits outermost so its ``call_tool`` hosts the tool span,
    per-model-request cap, and MCP-result spill over every dispatched tool, while
    ``get_tools`` (and thus per-turn visibility) still flows through the filter.
    """
    from co_cli.agent.toolset import _RoutingToolset, _tool_visibility_filter

    combined = CombinedToolset([native_toolset, *mcp_toolsets])
    filtered = combined.filtered(_tool_visibility_filter)
    return _RoutingToolset(filtered)
