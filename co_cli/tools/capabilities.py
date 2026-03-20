"""Capability introspection tool for the /doctor skill.

Returns a summary of active integrations and their health status so the
agent can report system state to the user in personality voice.
"""

from typing import Any

from pydantic_ai import RunContext

from co_cli.bootstrap._check import check_runtime
from co_cli.deps import CoDeps


async def check_capabilities(ctx: RunContext[CoDeps]) -> dict[str, Any]:
    """Return a summary of active capabilities and integration health.

    Returns a dict with:
    - display: formatted summary string for the user
    - knowledge_backend: active search backend ("fts5", "hybrid", or "grep")
    - reranker: active reranker provider name
    - google: True if Google credentials are configured
    - obsidian: True if Obsidian vault path is configured
    - brave: True if Brave Search API key is set
    - mcp_count: number of MCP servers configured
    - reasoning_model: name of the active reasoning model, or None
    - reasoning_ready: True if reasoning chain is configured
    - checks: list of {"name", "status", "detail"} for each probe
    - skill_grants: sorted list of active skill tool grants
    - tool_count: number of tools in the current session surface
    - active_skill: name of the currently active skill, or None
    - mcp_mode: "mcp" or "native-only"
    - knowledge_mode: active knowledge search backend
    """
    progress = ctx.deps.runtime.status_callback
    if progress is not None:
        progress("Doctor: starting runtime diagnostics...")
    result = check_runtime(ctx.deps, progress=progress)

    caps = result.capabilities
    st = result.status

    # Reranker from config
    _ce_url = ctx.deps.config.knowledge_cross_encoder_reranker_url
    _llm_r = ctx.deps.config.knowledge_llm_reranker
    if _ce_url:
        reranker = f"tei ({_ce_url})"
    elif _llm_r:
        reranker = f"{_llm_r.provider or 'llm'}:{_llm_r.model}"
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

    if ctx.deps.session.session_id:
        lines.append(f"Session: {ctx.deps.session.session_id[:8]}...")

    skill_grants = st["skill_grants"]
    if skill_grants:
        lines.append(f"Active skill grants: {', '.join(skill_grants)}")

    mcp_configured = len(ctx.deps.config.mcp_servers or {})
    mcp_live = caps["mcp_count"]
    # Invariant: tool_approvals keys == native tool names; tool_names = native + MCP (see build_agent)
    # This formula holds only while tool_names is seeded from tool_approvals.keys() in build_agent()
    # and extended exclusively by discover_mcp_tools(). Any future addition to tool_names outside
    # that path will silently corrupt this count.
    native_tool_count = len(ctx.deps.session.tool_approvals)
    mcp_tool_count = len(ctx.deps.session.tool_names) - native_tool_count

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

    return {
        "display": display,
        "knowledge_backend": caps["knowledge_backend"],
        "reranker": reranker,
        "google": caps["google"],
        "obsidian": caps["obsidian"],
        "brave": caps["brave"],
        "mcp_count": caps["mcp_count"],
        "mcp_configured_server_count": mcp_configured,
        "mcp_tool_count": mcp_tool_count,
        "mcp_server_health": [
            {"name": n, "ok": r.ok, "detail": r.detail} for n, r in result.mcp_probes
        ],
        "reasoning_model": reasoning_model,
        "reasoning_ready": caps["reasoning_ready"],
        "checks": caps["checks"],
        "skill_grants": skill_grants,
        "tool_count": st["tool_count"],
        "active_skill": st["active_skill"],
        "mcp_mode": st["mcp_mode"],
        "knowledge_mode": st["knowledge_mode"],
    }
