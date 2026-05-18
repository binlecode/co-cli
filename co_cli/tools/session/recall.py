"""Session recall tool — chunk-cited BM25 search over past session transcripts."""

import logging
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.observability.tracing import current_span
from co_cli.session.browser import list_sessions
from co_cli.session.filename import parse_session_filename
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)

_SESSIONS_CHANNEL_CAP = 3
"""Maximum unique sessions returned by session_search."""

_SNIPPET_DISPLAY_CHARS = 100
"""Maximum chars shown from a session chunk snippet in formatted output."""


def _browse_recent(
    ctx: RunContext[CoDeps],
    limit: int,
    span: Any,
) -> list[dict]:
    """Return recent-session metadata as a list of dicts — no LLM calls."""
    sessions = list_sessions(ctx.deps.sessions_dir)
    current_path = ctx.deps.session.session_path
    if current_path:
        current_resolved = current_path.resolve()
        sessions = [s for s in sessions if s.path.resolve() != current_resolved]
    sessions = sessions[:limit]
    span.set_attribute("memory.summarizer.runs", 0)
    span.set_attribute("memory.summarizer.failures", 0)
    span.set_attribute("memory.summarizer.timed_out", False)
    return [
        {
            "session_id": s.session_id,
            "when": s.created_at.isoformat()[:10],
            "title": s.title,
            "file_size": s.file_size,
        }
        for s in sessions
    ]


def _search_sessions(
    ctx: RunContext[CoDeps],
    query: str,
    span: Any,
) -> list[dict]:
    """Chunked recall over session transcripts via SessionStore.

    Returns chunk-cited dicts. Capped at _SESSIONS_CHANNEL_CAP unique sessions.
    """
    store = ctx.deps.session_store
    if store is None:
        span.set_attribute("memory.sessions.count", 0)
        return []

    try:
        raw = store.search(query, limit=_SESSIONS_CHANNEL_CAP * 5)
    except Exception as e:
        logger.warning("Session chunk search failed: %s", e)
        span.set_attribute("memory.sessions.count", 0)
        return []

    current_path = ctx.deps.session.session_path
    current_uuid8: str | None = None
    if current_path and current_path.name:
        parsed = parse_session_filename(current_path.name)
        if parsed is not None:
            current_uuid8 = parsed[0]

    seen: dict[str, Any] = {}
    for r in raw:
        session_uuid8 = r.path
        if session_uuid8 == current_uuid8:
            continue
        if session_uuid8 not in seen:
            seen[session_uuid8] = r
        if len(seen) >= _SESSIONS_CHANNEL_CAP:
            break

    span.set_attribute("memory.sessions.count", len(seen))

    results: list[dict] = []
    for session_uuid8, r in seen.items():
        when = r.created[:10] if r.created else ""
        results.append(
            {
                "session_id": session_uuid8,
                "when": when,
                "source": r.source,
                "chunk_text": r.snippet or "",
                "start_line": r.start_line,
                "end_line": r.end_line,
                "score": r.score,
            }
        )
    return results


def _format_session_results(query: str, results: list[dict]) -> str:
    lines: list[str] = [f"Found {len(results)} session result(s) for '{query}':\n"]
    for idx, entry in enumerate(results, 1):
        start = entry.get("start_line")
        end = entry.get("end_line")
        loc = f" @ L{start}-{end}" if start is not None and end is not None else ""
        lines.append(f"  {idx}. [{entry['when']}] {entry['session_id']}{loc}")
        preview = (entry.get("chunk_text") or "")[:_SNIPPET_DISPLAY_CHARS]
        if preview:
            lines.append(f"     {preview}")
    return "\n".join(lines)


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
)
async def session_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    limit: int = 3,
) -> ToolReturn:
    """Search past session transcripts by keyword, or browse recent sessions.

    USE THIS for past-conversation recall — "what did we figure out about X last time?",
    "what was that auth bug we hit?", "what were we working on yesterday?".

    Empty query → recent N session metadata (id, date, title) — browse mode.
    Non-empty query → BM25 chunk-cited search; results carry (session_id, when, source,
    chunk_text, start_line, end_line, score). Load verbatim turns with
    session_view(session_id, start_line, end_line).

    Args:
        query: FTS5 keyword query.
        limit: Max sessions returned (default 3).
    """
    span = current_span()
    limit = max(1, int(limit))
    query = query.strip() if query else ""

    if not query:
        session_results = _browse_recent(ctx, limit, span)
        if not session_results:
            return tool_output("No past sessions found.", ctx=ctx, count=0, results=[])
        lines = [f"Recent {len(session_results)} session(s):\n"]
        for idx, s in enumerate(session_results, 1):
            lines.append(f"{idx}. [{s['when']}] {s['session_id']} — {s['title']}")
        return tool_output(
            "\n".join(lines), ctx=ctx, count=len(session_results), results=session_results
        )

    session_results_raw = _search_sessions(ctx, query, span)
    if not session_results_raw:
        return tool_output(
            f"No session results found for '{query}'.", ctx=ctx, count=0, results=[]
        )
    return tool_output(
        _format_session_results(query, session_results_raw),
        ctx=ctx,
        count=len(session_results_raw),
        results=session_results_raw,
    )
