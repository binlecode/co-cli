"""MCP toolset building and tool discovery."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from pydantic_ai.toolsets import DeferredLoadingToolset

from co_cli.config._core import Settings
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MCPToolsetEntry:
    """MCP toolset paired with its build-time policy and direct server reference.

    ``server`` is the raw MCPServer (before approval_required() wrapping) so
    ``list_tools()`` can be called directly without walking the wrapper chain.
    ``approval``, ``prefix``, and ``timeout`` are recorded at build time; discovery
    reads them without inspecting wrapper topology.
    """

    toolset: DeferredLoadingToolset
    server: Any  # MCPServer subclass — lazily imported; avoids top-level pydantic_ai.mcp import
    approval: bool
    prefix: str
    timeout: float


def _build_mcp_toolsets(config: Settings) -> list[_MCPToolsetEntry]:
    """Build MCP toolsets and record their policy at construction time."""
    if not config.mcp_servers:
        return []
    from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio, MCPServerStreamableHTTP

    entries: list[_MCPToolsetEntry] = []
    for name, cfg in config.mcp_servers.items():
        if cfg.url:
            if cfg.url.rstrip("/").endswith("/sse"):
                mcp_server = MCPServerSSE(
                    cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout
                )
            else:
                mcp_server = MCPServerStreamableHTTP(
                    cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout
                )
        else:
            if cfg.command is None:
                logger.warning(
                    "MCP server %r: command required when url is not set — skipped", name
                )
                continue
            env = dict(cfg.env) if cfg.env else {}
            mcp_server = MCPServerStdio(
                cfg.command,
                args=cfg.args,
                timeout=cfg.timeout,
                env=env or None,
                tool_prefix=cfg.prefix or name,
            )
        approval = cfg.approval == "ask"
        inner = mcp_server.approval_required() if approval else mcp_server
        entries.append(
            _MCPToolsetEntry(
                toolset=DeferredLoadingToolset(inner),
                server=mcp_server,
                approval=approval,
                prefix=cfg.prefix or name,
                timeout=cfg.timeout,
            )
        )
    return entries


async def discover_mcp_tools(
    mcp_entries: list[_MCPToolsetEntry], exclude: set[str]
) -> tuple[list[str], dict[str, str], dict[str, ToolInfo]]:
    """Discover MCP tool names by connecting to servers and listing tools.

    Reads policy (approval, prefix) from the recorded ``_MCPToolsetEntry``; no
    wrapper chain walking. Returns (tool_names, errors, mcp_index) where errors
    maps server prefix to the error string for each server where list_tools()
    failed, and mcp_index maps tool name to ToolInfo metadata. Tool names exclude
    any in ``exclude``. MCP tools are deferred by default (DEFERRED visibility).
    """
    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}
    mcp_index: dict[str, ToolInfo] = {}

    for entry in mcp_entries:
        prefix = entry.prefix
        try:
            async with asyncio.timeout(entry.timeout):
                tools = await entry.server.list_tools()
            for t in tools:
                name = f"{prefix}_{t.name}" if prefix else t.name
                if name not in exclude:
                    mcp_tool_names.append(name)
                    mcp_index[name] = ToolInfo(
                        name=name,
                        description=t.description or "",
                        approval=entry.approval,
                        source=ToolSourceEnum.MCP,
                        visibility=VisibilityPolicyEnum.DEFERRED,
                        integration=prefix or None,
                    )
        except Exception as e:
            logger.warning("MCP tool list failed for %r: %s", prefix or "(no prefix)", e)
            errors[prefix] = str(e)

    return sorted(mcp_tool_names), errors, mcp_index
