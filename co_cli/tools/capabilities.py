"""Capability introspection tool for the /doctor skill.

Returns a summary of active integrations and their health status so the
agent can report system state to the user in personality voice.
"""

from typing import Any

from pydantic_ai import RunContext

from co_cli._doctor import run_doctor
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
    - checks: list of {"name", "status", "detail"} for each doctor check
    """
    result = run_doctor(ctx.deps)

    # Integration status derived from doctor checks
    google_item = result.by_name("google")
    google = google_item is not None and google_item.status == "ok"

    obsidian_item = result.by_name("obsidian")
    obsidian = obsidian_item is not None and obsidian_item.status == "ok"

    brave_item = result.by_name("brave")
    brave = brave_item is not None and brave_item.status == "ok"

    # Knowledge backend from config (unchanged)
    knowledge_backend = ctx.deps.config.knowledge_search_backend

    # Reranker from config
    reranker = ctx.deps.config.knowledge_reranker_provider

    # MCP count from config
    mcp_count = ctx.deps.config.mcp_count

    # Reasoning model chain from config
    reasoning_chain = ctx.deps.config.role_models.get("reasoning", [])

    # Build display: doctor summary lines + reranker, reasoning, session lines
    lines: list[str] = result.summary_lines()

    if reranker == "none":
        lines.append("Search reranker: disabled (search quality may be degraded)")
    else:
        lines.append(f"Search reranker: {reranker}")

    if reasoning_chain:
        lines.append(f"Reasoning models: {', '.join(e.model for e in reasoning_chain)}")
    else:
        lines.append("Reasoning models: not configured (doctor fail-fast)")

    if ctx.deps.config.session_id:
        lines.append(f"Session: {ctx.deps.config.session_id[:8]}...")

    skill_grants = sorted(ctx.deps.session.skill_tool_grants)
    if skill_grants:
        lines.append(f"Active skill grants: {', '.join(skill_grants)}")

    display = "\n".join(lines)

    checks = [
        {"name": c.name, "status": c.status, "detail": c.detail}
        for c in result.checks
    ]

    return {
        "display": display,
        "knowledge_backend": knowledge_backend,
        "reranker": reranker,
        "google": google,
        "obsidian": obsidian,
        "brave": brave,
        "mcp_count": mcp_count,
        "reasoning_models": reasoning_chain,
        "reasoning_ready": bool(reasoning_chain),
        "checks": checks,
        "skill_grants": skill_grants,
    }
