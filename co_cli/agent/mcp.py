"""MCP toolset building and tool discovery."""

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import AbstractToolset, DeferredLoadingToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from co_cli.config.core import Settings
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

logger = logging.getLogger(__name__)


class _SanitizingMCPServer:
    """Thin MCPServer proxy that sanitizes inputSchema on list_tools()."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def list_tools(self) -> list:
        from co_cli.tools.mcp_schema import sanitize_mcp_schema

        tools = await self._inner.list_tools()
        for t in tools:
            t.inputSchema = sanitize_mcp_schema(t.inputSchema or {})
        return tools

    async def __aenter__(self) -> "_SanitizingMCPServer":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._inner.__aexit__(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@dataclass
class _SequentialMCPToolset(WrapperToolset):
    """Patches ToolDefinition.sequential from tool_index.is_concurrent_safe for MCP tools.

    Holds a mutable reference to tool_index — populated by discover_mcp_tools()
    before the first get_tools() call, so the patch is always current at step time.
    """

    tool_index: dict[str, ToolInfo]

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: replace(
                tool,
                tool_def=replace(tool.tool_def, sequential=not info.is_concurrent_safe),
            )
            if (info := self.tool_index.get(name)) is not None
            else tool
            for name, tool in tools.items()
        }


@dataclass(frozen=True)
class MCPToolsetEntry:
    """MCP toolset paired with its build-time policy and direct server reference.

    ``server`` is a ``_SanitizingMCPServer`` wrapping the raw MCPServer (before
    ``approval_required()`` wrapping) so ``list_tools()`` returns sanitized schemas
    and can be called directly without walking the wrapper chain.
    ``approval``, ``prefix``, and ``timeout`` are recorded at build time; discovery
    reads them without inspecting wrapper topology.
    """

    toolset: AbstractToolset
    server: Any  # MCPServer subclass — lazily imported; avoids top-level pydantic_ai.mcp import
    approval: bool
    prefix: str
    timeout: float


def _build_mcp_toolsets(config: Settings) -> list[MCPToolsetEntry]:
    """Build MCP toolsets and record their policy at construction time."""
    if not config.mcp_servers:
        return []
    from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio, MCPServerStreamableHTTP

    entries: list[MCPToolsetEntry] = []
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
        sanitizing_server = _SanitizingMCPServer(mcp_server)
        inner = sanitizing_server.approval_required() if approval else sanitizing_server
        entries.append(
            MCPToolsetEntry(
                toolset=DeferredLoadingToolset(inner),
                server=sanitizing_server,
                approval=approval,
                prefix=cfg.prefix or name,
                timeout=cfg.timeout,
            )
        )
    return entries


async def _discover_one(
    entry: MCPToolsetEntry, exclude: set[str]
) -> tuple[list[tuple[str, ToolInfo]], str | None]:
    prefix = entry.prefix
    try:
        async with asyncio.timeout(entry.timeout):
            tools = await entry.server.list_tools()
        hits: list[tuple[str, ToolInfo]] = []
        for t in tools:
            name = f"{prefix}_{t.name}" if prefix else t.name
            if name not in exclude:
                hits.append(
                    (
                        name,
                        ToolInfo(
                            name=name,
                            description=t.description or "",
                            approval=entry.approval,
                            source=ToolSourceEnum.MCP,
                            visibility=VisibilityPolicyEnum.DEFERRED,
                            integration=prefix or None,
                        ),
                    )
                )
        return hits, None
    except Exception as e:
        logger.warning("MCP tool list failed for %r: %s", prefix or "(no prefix)", e)
        return [], str(e)


async def discover_mcp_tools(
    mcp_entries: list[MCPToolsetEntry], exclude: set[str]
) -> tuple[list[str], dict[str, str], dict[str, ToolInfo]]:
    """Discover MCP tool names by connecting to servers and listing tools.

    Reads policy (approval, prefix) from the recorded ``MCPToolsetEntry``; no
    wrapper chain walking. Returns (tool_names, errors, mcp_index) where errors
    maps server prefix to the error string for each server where list_tools()
    failed, and mcp_index maps tool name to ToolInfo metadata. Tool names exclude
    any in ``exclude``. MCP tools are deferred by default (DEFERRED visibility).
    All servers are queried concurrently; startup delay is max(timeouts) not sum.
    """
    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}
    mcp_index: dict[str, ToolInfo] = {}

    results = await asyncio.gather(*[_discover_one(entry, exclude) for entry in mcp_entries])
    for entry, (hits, err) in zip(mcp_entries, results, strict=True):
        if err is not None:
            errors[entry.prefix] = err
        for name, info in hits:
            mcp_tool_names.append(name)
            mcp_index[name] = info

    return sorted(mcp_tool_names), errors, mcp_index
