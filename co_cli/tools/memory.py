"""Memory tools — episodic recall over session transcripts."""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools._agent_tool import agent_tool
from co_cli.tools.session_search import session_search


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def search_memory(
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

    Do NOT use for saved preferences, rules, project conventions, or reusable knowledge artifacts — use search_knowledge for those. This tool searches what was SAID in past conversations; search_knowledge searches what was DISTILLED from them.

    Args:
        query: FTS5 keyword query (see syntax above).
        limit: Max results to return (default 5).
    """
    return await session_search(ctx, query, limit)
