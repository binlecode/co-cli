"""Session search tool — keyword search over past session transcripts."""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools.tool_output import tool_output


async def session_search(ctx: RunContext[CoDeps], query: str, limit: int = 3) -> ToolReturn:
    """Search past session transcripts by keyword and return ranked excerpts.

    Searches the local session index (FTS5/BM25) built from all previous chat
    sessions in this project.  Returns the highest-scoring excerpt per session,
    up to limit results.  Returns an empty-result message when the index is
    unavailable (DB error, first run, or degraded startup).
    """
    store = ctx.deps.session_index
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
