"""Memory tools — episodic memory (conversation transcripts).

Contains only search_memories; all artifact-layer operations moved to
tools/knowledge.py during the rename-memory-tools-to-knowledge refactor.
"""

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools.session_search import session_search


async def search_memories(
    ctx: RunContext[CoDeps],
    query: str,
    *,
    limit: int = 5,
) -> ToolReturn:
    """Search episodic memory — past conversation transcripts.

    Searches session transcripts by keyword and returns ranked excerpts.
    For saved preferences, rules, and knowledge artifacts, use search_knowledge.

    Args:
        query: Free-text search query.
        limit: Max results to return (default 5).
    """
    return await session_search(ctx, query, limit)
