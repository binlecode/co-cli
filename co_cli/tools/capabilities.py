"""Capability introspection tool for the /doctor skill.

Returns a summary of active integrations and their health status so the
agent can report system state to the user in personality voice.
"""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.bootstrap.check import check_runtime
from co_cli.deps import CoDeps
from co_cli.tools.tool_io import tool_output


async def check_capabilities(ctx: RunContext[CoDeps]) -> ToolReturn:
    """Return a summary of active capabilities and integration health.

    Returns a ToolReturn with display (formatted summary string) and metadata:
    knowledge_backend, reranker, google, obsidian, brave, mcp_count,
    reasoning_model, reasoning_ready, checks, tool_count,
    active_skill, mcp_mode, knowledge_mode.
    """
    progress = ctx.deps.runtime.tool_progress_callback
    if progress is not None:
        progress("Doctor: starting runtime diagnostics...")
    result = check_runtime(ctx.deps, progress=progress)

    caps = result.capabilities
    st = result.status

    # Reranker from config
    _ce_url = ctx.deps.config.knowledge.cross_encoder_reranker_url
    _llm_r = ctx.deps.config.knowledge.llm_reranker
    if _ce_url:
        reranker = f"tei ({_ce_url})"
    elif _llm_r:
        reranker = f"{_llm_r.provider}:{_llm_r.model}"
    else:
        reranker = "none"

    # Reasoning model sourced from capabilities
    reasoning_model = caps["reasoning_model"]

    # Build display: probe summary lines + reranker, reasoning, session lines
    lines: list[str] = result.summary_lines()

    if reranker == "none":
        lines.append("Search reranker: disabled (search quality may be degraded)")
    else:
        lines.append(f"Search reranker: {reranker}")

    if reasoning_model:
        lines.append(f"Reasoning model: {reasoning_model}")
    else:
        lines.append("Reasoning model: not configured (doctor fail-fast)")

    short_id = ctx.deps.session.session_path.stem[-8:]
    if short_id:
        lines.append(f"Session: {short_id}...")

    mcp_configured = len(ctx.deps.config.mcp_servers or {})
    mcp_live = caps["mcp_count"]
    tool_index = ctx.deps.tool_index
    native_tool_count = sum(1 for tc in tool_index.values() if tc.source == "native")
    mcp_tool_count = sum(1 for tc in tool_index.values() if tc.source == "mcp")

    # Source breakdown
    source_counts = st.get("source_counts", {})
    if source_counts:
        source_parts = ", ".join(
            f"{source}: {count}" for source, count in sorted(source_counts.items())
        )
        lines.append(f"Tools by source: {source_parts}")

    if mcp_configured == 0:
        lines.append("MCP: none configured")
    else:
        lines.append(
            f"MCP: {mcp_live}/{mcp_configured} servers connected · {mcp_tool_count} tools"
        )
        for name, probe in result.mcp_probes:
            status_str = "ok" if probe.ok else f"degraded — {probe.detail}"
            lines.append(f"  {name}: {status_str}")

    display = "\n".join(lines)

    return tool_output(
        display,
        ctx=ctx,
        knowledge_backend=caps["knowledge_backend"],
        reranker=reranker,
        google=caps["google"],
        obsidian=caps["obsidian"],
        brave=caps["brave"],
        mcp_count=caps["mcp_count"],
        mcp_configured_server_count=mcp_configured,
        mcp_tool_count=mcp_tool_count,
        native_tool_count=native_tool_count,
        source_counts=source_counts,
        mcp_server_health=[
            {"name": n, "ok": r.ok, "detail": r.detail} for n, r in result.mcp_probes
        ],
        reasoning_model=reasoning_model,
        reasoning_ready=caps["reasoning_ready"],
        checks=caps["checks"],
        tool_count=st["tool_count"],
        active_skill=st["active_skill"],
        mcp_mode=st["mcp_mode"],
        knowledge_mode=st["knowledge_mode"],
    )
