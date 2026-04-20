"""Memory tools — episodic recall over session transcripts."""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def memory_search(
    ctx: RunContext[CoDeps],
    query: str,
    *,
    limit: int = 5,
) -> ToolReturn:
    """Search episodic memory — past conversation transcripts across all sessions in this project. Returns ranked excerpts with session ID, date, and matching snippet.

    USE THIS PROACTIVELY when:
    - The user says "we did this before", "remember when", "last time", "as I mentioned"
    - The user asks about a topic you've worked on but don't have in current context
    - The user references a project, person, decision, or concept that seems familiar but isn't in the current session
    - You want to check if a similar problem has been solved before
    - The user asks "what did we do about X?" or "how did we fix Y?"

    Don't hesitate — search is local FTS5 (BM25), fast, zero LLM cost. Better to search and confirm than to guess or ask the user to repeat themselves.

    Search syntax (FTS5): keywords joined with OR for broad recall (auth OR login OR session), phrases for exact match ("connection pool"), boolean (python NOT java), prefix (deploy*). IMPORTANT: FTS5 defaults to AND between terms — use explicit OR for broader matches. If a broad OR query returns nothing, try individual keywords.

    Do NOT use for saved preferences, rules, project conventions, or reusable knowledge artifacts — use knowledge_search for those. This tool searches what was SAID in past conversations; knowledge_search searches what was DISTILLED from them.

    Args:
        query: FTS5 keyword query (see syntax above).
        limit: Max results to return (default 5).
    """
    store = ctx.deps.memory_index
    if store is None:
        return tool_output(
            "Session index is not available — no past sessions have been indexed yet.",
            ctx=ctx,
            count=0,
            results=[],
        )

    results = store.search(query, limit=limit)
    if not results:
        return tool_output(
            f"No past sessions matched '{query}'.",
            ctx=ctx,
            count=0,
            results=[],
        )

    lines: list[str] = [f"Found {len(results)} session(s) matching '{query}':\n"]
    for idx, result in enumerate(results, 1):
        lines.append(
            f"{idx}. [{result.created_at[:10]}] {result.session_id} ({result.role})\n"
            f"   {result.snippet}\n"
            f"   score={result.score:.3f} | path={result.session_path}"
        )

    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(results),
        results=[
            {
                "session_id": r.session_id,
                "session_path": r.session_path,
                "created_at": r.created_at,
                "role": r.role,
                "snippet": r.snippet,
                "score": r.score,
            }
            for r in results
        ],
    )
