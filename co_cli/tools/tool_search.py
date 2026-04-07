"""Progressive tool discovery — search_tools discovers deferred tools into the session."""

import re

from pydantic_ai import RunContext

from co_cli.deps import CoDeps, ToolInfo, LoadPolicy
from pydantic_ai.messages import ToolReturn

from co_cli.tools.tool_output import tool_output


async def search_tools(ctx: RunContext[CoDeps], query: str, max_results: int = 8) -> ToolReturn:
    """Discover and unlock additional tools by keyword search.

    Searches tool name, description, integration, and search_hint. Matched
    deferred tools are added to session.discovered_tools and become callable
    on the next step. Always-loaded tools matching the query are reported
    as 'already available'.
    """
    tool_index = ctx.deps.tool_index
    query_tokens = set(re.split(r"[_\s\-]+", query.lower()))

    # Exact-name lookup across all tools first
    exact_match = tool_index.get(query.strip())

    scored: list[tuple[int, str, ToolInfo]] = []
    for name, tc in tool_index.items():
        search_text = f"{name} {tc.description}"
        if tc.integration:
            search_text += f" {tc.integration}"
        if tc.search_hint:
            search_text += f" {tc.search_hint}"
        search_text = search_text.lower()
        search_tokens = set(re.split(r"[_\s\-]+", search_text))
        score = sum(1 for t in query_tokens if t in search_tokens)
        if score > 0:
            scored.append((score, name, tc))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_results]

    if not top and exact_match is None:
        return tool_output(
            f"No tools found for {query!r}. "
            "Try: 'edit file', 'save memory', 'background task', 'sub-agent', 'gmail'.",
            ctx=ctx,
        )

    # Include exact match if not already in scored results
    top_names = {name for _, name, _ in top}
    if exact_match is not None and exact_match.name not in top_names:
        top.insert(0, (999, exact_match.name, exact_match))

    discovered_now: list[str] = []
    lines = [f"Found {len(top)} tool(s):"]
    for _, name, tc in top:
        if tc.load == LoadPolicy.ALWAYS:
            status = "already available"
        elif name in ctx.deps.session.discovered_tools:
            status = "already available"
        else:
            status = "unlocked"
            discovered_now.append(name)

        integration_tag = f" ({tc.integration})" if tc.integration else ""
        lines.append(f"  {name} {status}{integration_tag}: {tc.description}")

    ctx.deps.session.discovered_tools.update(discovered_now)

    if discovered_now:
        lines.append(f"\n{len(discovered_now)} tool(s) unlocked. Call them in your next step.")
    return tool_output("\n".join(lines), ctx=ctx, count=len(top), granted=discovered_now)
