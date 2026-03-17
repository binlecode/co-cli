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
    - reasoning_models: list of reasoning ModelEntry objects
    - reasoning_ready: True if reasoning chain is configured
    - checks: list of {"name", "status", "detail"} for each probe
    - skill_grants: sorted list of active skill tool grants
    - tool_count: number of tools in the current session surface
    - active_skill: name of the currently active skill, or None
    - mcp_mode: "mcp" or "native-only"
    - knowledge_mode: active knowledge search backend
    """
    result = check_runtime(ctx.deps)

    caps = result.capabilities
    st = result.status

    # Reranker from config
    reranker = ctx.deps.config.knowledge_reranker_provider

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

    if ctx.deps.config.session_id:
        lines.append(f"Session: {ctx.deps.config.session_id[:8]}...")

    skill_grants = st["skill_grants"]
    if skill_grants:
        lines.append(f"Active skill grants: {', '.join(skill_grants)}")

    if st["tool_count"]:
        lines.append(f"Tools: {st['tool_count']} ({st['mcp_mode']})")

    display = "\n".join(lines)

    return {
        "display": display,
        "knowledge_backend": caps["knowledge_backend"],
        "reranker": reranker,
        "google": caps["google"],
        "obsidian": caps["obsidian"],
        "brave": caps["brave"],
        "mcp_count": caps["mcp_count"],
        "reasoning_model": reasoning_model,
        "reasoning_ready": caps["reasoning_ready"],
        "checks": caps["checks"],
        "skill_grants": skill_grants,
        "tool_count": st["tool_count"],
        "active_skill": st["active_skill"],
        "mcp_mode": st["mcp_mode"],
        "knowledge_mode": st["knowledge_mode"],
    }
