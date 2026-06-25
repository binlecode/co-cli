"""MCP toolset building and tool discovery."""

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import AbstractToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from co_cli.config.core import Settings
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

logger = logging.getLogger(__name__)


@dataclass
class _SequentialMCPToolset(WrapperToolset):
    """Sanitizes MCP tool schemas and patches ToolDefinition.sequential at the toolset seam.

    Schema sanitization happens here — on the converted ``tool_def.parameters_json_schema``
    in the ``get_tools`` output (WrapperToolset.get_tools, toolsets/wrapper.py:60) — rather
    than on the raw ``inputSchema`` before conversion. The SDK assigns the raw MCP
    ``inputSchema`` verbatim to ``parameters_json_schema`` (pydantic_ai/mcp.py:667), so
    sanitizing the converted schema is lossless: it operates on the same dict structure
    ``sanitize_mcp_schema`` expects.

    ``sequential`` is patched from tool_catalog.is_concurrent_safe. tool_catalog is a mutable
    reference — populated by discover_mcp_tools() before the first get_tools() call, so the
    patch is always current at step time.
    """

    tool_catalog: dict[str, ToolInfo]

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        from co_cli.tools.mcp_schema import sanitize_mcp_schema

        tools = await self.wrapped.get_tools(ctx)
        result: dict[str, ToolsetTool[Any]] = {}
        for name, tool in tools.items():
            sequential = (
                not info.is_concurrent_safe
                if (info := self.tool_catalog.get(name)) is not None
                else tool.tool_def.sequential
            )
            sanitized_schema = sanitize_mcp_schema(tool.tool_def.parameters_json_schema or {})
            result[name] = replace(
                tool,
                tool_def=replace(
                    tool.tool_def,
                    parameters_json_schema=sanitized_schema,
                    sequential=sequential,
                ),
            )
        return result


@dataclass(frozen=True)
class MCPToolsetEntry:
    """MCP toolset paired with its build-time policy and direct server reference.

    ``server`` is the raw MCPServer (before ``approval_required()`` wrapping) — a thin
    direct handle for discovery's ``list_tools()`` call, bypassing the wrapper chain.
    Discovery reads only ``t.name`` / ``t.description`` (not ``inputSchema``), so it does
    not need sanitized schemas; sanitization lives at the toolset seam
    (``_SequentialMCPToolset.get_tools``), on the converted ``parameters_json_schema``.
    ``is_approval_required``, ``prefix``, and ``connect_timeout_seconds`` are recorded at
    build time; connection and discovery read them without inspecting wrapper topology.
    ``connect_timeout_seconds`` bounds connection/discovery (pydantic-ai ``timeout``).
    The per-call response bound (``call_timeout_seconds`` → pydantic-ai ``read_timeout``)
    is applied by the SDK from the constructed server, so it is not re-recorded here.
    """

    toolset: AbstractToolset
    server: Any  # MCPServer subclass — lazily imported; avoids top-level pydantic_ai.mcp import
    is_approval_required: bool
    prefix: str
    connect_timeout_seconds: float


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
                    cfg.url,
                    tool_prefix=cfg.prefix or name,
                    timeout=cfg.connect_timeout_seconds,
                    read_timeout=cfg.call_timeout_seconds,
                )
            else:
                mcp_server = MCPServerStreamableHTTP(
                    cfg.url,
                    tool_prefix=cfg.prefix or name,
                    timeout=cfg.connect_timeout_seconds,
                    read_timeout=cfg.call_timeout_seconds,
                )
        else:
            if cfg.command is None:
                logger.warning(
                    "MCP server %r: command required when url is not set — skipped", name
                )
                continue
            mcp_server = MCPServerStdio(
                cfg.command,
                args=cfg.args,
                timeout=cfg.connect_timeout_seconds,
                read_timeout=cfg.call_timeout_seconds,
                env=cfg.env or None,
                tool_prefix=cfg.prefix or name,
            )
        is_approval_required = cfg.approval == "ask"
        inner = mcp_server.approval_required() if is_approval_required else mcp_server
        # No DeferredLoadingToolset: it stamps defer_loading=True, which would re-engage
        # the SDK's search_tools loader. MCP tools are DEFERRED in tool_catalog, so co's
        # per-turn visibility filter (agent/toolset.py) hides them until loaded via
        # tool_view — one loader, native and MCP alike.
        entries.append(
            MCPToolsetEntry(
                toolset=inner,
                server=mcp_server,
                is_approval_required=is_approval_required,
                prefix=cfg.prefix or name,
                connect_timeout_seconds=cfg.connect_timeout_seconds,
            )
        )
    return entries


async def _discover_one(
    entry: MCPToolsetEntry, exclude: set[str]
) -> tuple[list[tuple[str, ToolInfo]], str | None]:
    prefix = entry.prefix
    try:
        # Thin direct handle: list_tools() on the raw server, bypassing the wrapper chain.
        # Discovery reads only t.name / t.description below — never inputSchema — so it
        # does not need the sanitized schema (that lives at the _SequentialMCPToolset seam).
        async with asyncio.timeout(entry.connect_timeout_seconds):
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
                            is_approval_required=entry.is_approval_required,
                            source=ToolSourceEnum.MCP,
                            visibility=VisibilityPolicyEnum.DEFERRED,
                            # third-party MCP tools run sequentially until proven concurrent-safe
                            is_concurrent_safe=False,
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
    wrapper chain walking. Returns (tool_names, errors, mcp_tool_catalog) where errors
    maps server prefix to the error string for each server where list_tools()
    failed, and mcp_tool_catalog maps tool name to ToolInfo metadata. Tool names exclude
    any in ``exclude``. MCP tools are deferred by default (DEFERRED visibility).
    All servers are queried concurrently; startup delay is max(timeouts) not sum.
    """
    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}
    mcp_tool_catalog: dict[str, ToolInfo] = {}

    results = await asyncio.gather(*[_discover_one(entry, exclude) for entry in mcp_entries])
    for entry, (hits, err) in zip(mcp_entries, results, strict=True):
        if err is not None:
            errors[entry.prefix] = err
        for name, info in hits:
            mcp_tool_names.append(name)
            mcp_tool_catalog[name] = info

    return sorted(mcp_tool_names), errors, mcp_tool_catalog
