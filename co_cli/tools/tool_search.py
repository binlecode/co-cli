"""Progressive tool discovery — search_tools grants discoverable tools into the session."""

from pydantic_ai import RunContext

from co_cli.deps import CoDeps, ToolConfig
from co_cli.tools._result import ToolResult, make_result


async def search_tools(ctx: RunContext[CoDeps], query: str, max_results: int = 8) -> ToolResult:
    """Discover and unlock additional tools by keyword search over the tool catalog.

    Searches tool name, description, and family. Matched tools are granted into
    the session surface and become callable on the next step. Core tools already
    in the active surface are reported as 'already available'.

    Use this to find: file-write tools, memory-save tools, background tasks,
    sub-agents, or connector tools (Obsidian, Google Drive, Gmail, Calendar).
    """
    catalog = ctx.deps.capabilities.tool_catalog
    query_tokens = set(query.lower().split())
    scored: list[tuple[int, str, ToolConfig]] = []
    for name, tc in catalog.items():
        text = f"{name} {tc.description} {tc.family}".lower()
        score = sum(1 for t in query_tokens if t in text)
        if score > 0:
            scored.append((score, name, tc))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_results]
    if not top:
        # NOTE: keep this hint in sync with the discoverable tool families
        # (save/memory/article/file/task/subagent/connectors) as the catalog evolves.
        return make_result(
            f"No tools found for {query!r}. "
            "Try: 'save memory', 'edit file', 'background task', 'sub-agent', 'gmail'.",
        )
    active = ctx.deps.runtime.active_tool_filter or set()
    candidates = [name for _, name, _ in top]
    # Compute grants atomically: determine new grants first, then update in one call.
    granted_now = [n for n in candidates if n not in active]
    ctx.deps.session.granted_tools.update(granted_now)
    lines = [f"Found {len(top)} tool(s):"]
    for _, name, tc in top:
        status = "already available" if name not in granted_now else "unlocked"
        lines.append(f"  {name} [{tc.family}] {status}: {tc.description}")
    if granted_now:
        lines.append(f"\n{len(granted_now)} tool(s) unlocked. Call them in your next step.")
    return make_result("\n".join(lines), count=len(top), granted=granted_now)
