"""MCP toolset building and tool discovery."""

import logging

from pydantic_ai.toolsets import DeferredLoadingToolset

from co_cli.config._core import Settings
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

logger = logging.getLogger(__name__)


def _build_mcp_toolsets(config: Settings) -> list:
    """Build MCP toolsets wrapped with DeferredLoadingToolset for SDK-native discovery."""
    if not config.mcp_servers:
        return []
    from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio, MCPServerStreamableHTTP

    mcp_toolsets = []
    for name, cfg in config.mcp_servers.items():
        if cfg.url:
            # HTTP transport — SSE when URL ends with /sse, else StreamableHTTP
            if cfg.url.rstrip("/").endswith("/sse"):
                server = MCPServerSSE(cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout)
            else:
                server = MCPServerStreamableHTTP(
                    cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout
                )
        else:
            if cfg.command is None:
                logger.warning(
                    "MCP server %r: command required when url is not set — skipped", name
                )
                continue
            env = dict(cfg.env) if cfg.env else {}
            server = MCPServerStdio(
                cfg.command,
                args=cfg.args,
                timeout=cfg.timeout,
                env=env or None,
                tool_prefix=cfg.prefix or name,
            )
        if cfg.approval == "ask":
            server = server.approval_required()
        mcp_toolsets.append(DeferredLoadingToolset(server))
    return mcp_toolsets


async def discover_mcp_tools(
    mcp_toolsets: list, exclude: set[str]
) -> tuple[list[str], dict[str, str], dict[str, ToolInfo]]:
    """Discover MCP tool names by connecting to servers and listing tools.

    Each server self-connects on list_tools() (pydantic-ai lazy init).
    Walks the .wrapped chain recursively to find MCPServer instances
    (handles DeferredLoadingToolset and ApprovalRequiredToolset wrappers).
    Returns (tool_names, errors, mcp_index) where errors maps server prefix to
    the error string for each server where list_tools() failed, and mcp_index maps
    tool name to ToolInfo metadata. Tool names exclude any in ``exclude``.
    MCP tools are deferred by default (visibility=VisibilityPolicyEnum.DEFERRED).
    """
    from pydantic_ai.mcp import MCPServer

    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}
    mcp_index: dict[str, ToolInfo] = {}

    for toolset in mcp_toolsets:
        # Walk .wrapped chain recursively to find MCPServer
        inner = toolset
        wrapper_count = 0
        while hasattr(inner, "wrapped"):
            inner = inner.wrapped
            wrapper_count += 1
        if not isinstance(inner, MCPServer):
            continue
        prefix = inner.tool_prefix or ""
        try:
            tools = await inner.list_tools()
            for t in tools:
                name = f"{prefix}_{t.name}" if prefix else t.name
                if name not in exclude:
                    mcp_tool_names.append(name)
                    # DeferredLoadingToolset adds 1 wrapper level;
                    # extra levels indicate an approval wrapper
                    approval = wrapper_count > 1
                    mcp_index[name] = ToolInfo(
                        name=name,
                        description=t.description or "",
                        approval=approval,
                        source=ToolSourceEnum.MCP,
                        visibility=VisibilityPolicyEnum.DEFERRED,
                        integration=prefix or None,
                    )
        except Exception as e:
            logger.warning("MCP tool list failed for %r: %s", prefix or "(no prefix)", e)
            errors[prefix] = str(e)

    return sorted(mcp_tool_names), errors, mcp_index
