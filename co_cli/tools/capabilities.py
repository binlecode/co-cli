"""Capability introspection tool for the /doctor skill.

Returns a summary of active integrations and their health status so the
agent can report system state to the user in personality voice.
"""

from typing import Any

from pydantic_ai import RunContext

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
    """
    lines: list[str] = []

    # Knowledge search backend
    if ctx.deps.knowledge_index is not None:
        knowledge_backend = ctx.deps.knowledge_search_backend
        lines.append(f"Knowledge search: {knowledge_backend} (active)")
    else:
        knowledge_backend = "grep"
        lines.append("Knowledge search: grep (FTS5 index unavailable)")

    # Reranker
    reranker = ctx.deps.knowledge_reranker_provider
    if reranker == "none":
        lines.append("Search reranker: disabled (search quality may be degraded)")
    else:
        lines.append(f"Search reranker: {reranker}")

    # Google
    google = ctx.deps.google_credentials_path is not None
    lines.append(f"Google (Drive/Gmail/Calendar): {'configured' if google else 'not configured'}")

    # Obsidian
    obsidian = ctx.deps.obsidian_vault_path is not None
    lines.append(f"Obsidian notes: {'configured' if obsidian else 'not configured'}")

    # Brave web search
    brave = ctx.deps.brave_search_api_key is not None
    lines.append(f"Web search (Brave): {'configured' if brave else 'not configured'}")

    # MCP servers
    mcp_count = ctx.deps.mcp_count
    lines.append(f"MCP servers: {mcp_count} configured")

    # Main reasoning role (mandatory for model invocation)
    reasoning_chain = ctx.deps.model_roles.get("reasoning", [])
    if reasoning_chain:
        lines.append(f"Reasoning models: {', '.join(reasoning_chain)}")
    else:
        lines.append("Reasoning models: not configured (doctor fail-fast)")

    # Session info
    if ctx.deps.session_id:
        lines.append(f"Session: {ctx.deps.session_id[:8]}...")

    display = "\n".join(lines)

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
    }
