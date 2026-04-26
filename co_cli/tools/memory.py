"""Memory tools — episodic recall over session transcripts."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory._summary import (
    _format_conversation,
    _truncate_around_matches,
    summarize_session_around_query,
)
from co_cli.memory.session_browser import list_sessions
from co_cli.memory.store import SessionSearchResult
from co_cli.memory.transcript import load_transcript
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)


def _browse_recent(
    ctx: RunContext[CoDeps],
    limit: int,
    span: Span,
) -> ToolReturn:
    """Return recent-session metadata — no LLM calls."""
    sessions = list_sessions(ctx.deps.sessions_dir)
    current_path = ctx.deps.session.session_path
    if current_path:
        current_resolved = current_path.resolve()
        sessions = [s for s in sessions if s.path.resolve() != current_resolved]
    sessions = sessions[:limit]

    span.set_attribute("memory.summarizer.runs", 0)
    span.set_attribute("memory.summarizer.failures", 0)
    span.set_attribute("memory.summarizer.timed_out", False)

    if not sessions:
        return tool_output("No past sessions found.", ctx=ctx, count=0, results=[])

    lines = [f"Recent {len(sessions)} session(s):\n"]
    for idx, s in enumerate(sessions, 1):
        lines.append(f"{idx}. [{s.created_at.isoformat()[:10]}] {s.session_id} — {s.title}")
    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(sessions),
        results=[
            {
                "session_id": s.session_id,
                "when": s.created_at.isoformat()[:10],
                "title": s.title,
                "file_size": s.file_size,
            }
            for s in sessions
        ],
    )


def _dedup_sessions(
    raw_results: list[SessionSearchResult],
    current_resolved: Any,
    limit: int,
) -> dict[str, SessionSearchResult]:
    """Deduplicate FTS5 results to one entry per session, skipping current session."""
    seen: dict[str, SessionSearchResult] = {}
    for result in raw_results:
        if result.session_id in seen:
            continue
        if current_resolved and Path(result.session_path).resolve() == current_resolved:
            continue
        seen[result.session_id] = result
        if len(seen) >= limit:
            break
    return seen


def _prepare_tasks(
    seen: dict[str, SessionSearchResult],
    query: str,
) -> list[tuple[str, SessionSearchResult, str]]:
    """Load transcripts and build pre-formatted windows for summarization."""
    tasks: list[tuple[str, SessionSearchResult, str]] = []
    for session_id, match_info in seen.items():
        try:
            messages = load_transcript(Path(match_info.session_path))
            if not messages:
                continue
            window = _truncate_around_matches(_format_conversation(messages), query)
            tasks.append((session_id, match_info, window))
        except Exception as e:
            logger.warning("Failed to prepare session %s: %s", session_id, e, exc_info=True)
    return tasks


def _build_results_payload(
    tasks: list[tuple[str, SessionSearchResult, str]],
    summaries: list[Any],
) -> tuple[list[dict], int]:
    """Merge summarizer results with fallback previews for failures."""
    results_payload: list[dict] = []
    failure_count = 0
    for (session_id, match_info, window), summary_result in zip(tasks, summaries, strict=True):
        if isinstance(summary_result, Exception) or not summary_result:
            failure_count += 1
            preview = window[:500] + "\n…[truncated]" if window else "No preview available."
            summary = f"[Raw preview — summarization unavailable]\n{preview}"
        else:
            summary = summary_result
        results_payload.append(
            {
                "session_id": session_id,
                "when": match_info.created_at[:10],
                "source": match_info.session_path,
                "summary": summary,
            }
        )
    return results_payload, failure_count


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def memory_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    *,
    limit: int = 3,
) -> ToolReturn:
    """Search episodic memory — past conversation transcripts across all sessions in this project.

    TWO MODES:
    (a) Empty query → recent-sessions metadata, zero LLM cost. Use when the user asks
        what was worked on recently, what we did last time, or to browse session history.
    (b) Keyword query → LLM-summarized recaps of matching sessions. Returns per-session
        prose summaries with metadata, not raw snippets. Summaries run in parallel via
        a cheap noreason model; limit is clamped to [1, 5] (default 3).

    USE THIS PROACTIVELY when:
    - The user says "we did this before", "remember when", "last time", "as I mentioned"
    - The user asks about a topic you've worked on but don't have in current context
    - The user references a project, person, decision, or concept that seems familiar but isn't in the current session
    - The user asks "what did we do about X?" or "how did we fix Y?"
    - The user asks "what did we talk about regarding X?" — even if X is a preference or convention topic

    DISAMBIGUATION — when both memory_search and knowledge_search could apply:
    Call knowledge_search first (cheap, BM25 only, no LLM cost) — only fall back to memory_search
    if knowledge returns nothing, OR if the user is specifically asking about a past conversation.

    Concrete examples:
    - "what was my preferred test runner?" → knowledge_search (saved preference)
    - "what did we figure out about docker last time?" → memory_search (past conversation)
    - "what was that auth bug we hit?" → memory_search (past conversation)
    - "what's our convention for logging?" → knowledge_search (saved rule/decision)

    Do NOT use for saved preferences, rules, project conventions, or reusable knowledge artifacts
    — use knowledge_search for those. This tool searches what was SAID in past conversations;
    knowledge_search searches what was DISTILLED and curated from them.

    Search syntax (FTS5): keywords joined with OR for broad recall (auth OR login OR session),
    phrases for exact match ("connection pool"), boolean (python NOT java), prefix (deploy*).
    IMPORTANT: FTS5 defaults to AND between terms — use explicit OR for broader matches.

    Args:
        query: FTS5 keyword query. Omit or pass empty string for recent-sessions browse mode.
        limit: Max sessions to return/summarize, clamped to [1, 5] (default 3).
    """
    span = otel_trace.get_current_span()

    store = ctx.deps.memory_index
    if store is None:
        return tool_output(
            "Session index is not available — no past sessions have been indexed yet.",
            ctx=ctx,
            count=0,
            results=[],
        )

    limit = max(1, min(int(limit), 5))

    if not query or not query.strip():
        return _browse_recent(ctx, limit, span)

    query = query.strip()

    raw_results = store.search(query, limit=limit * 5)
    if not raw_results:
        return tool_output(
            f"No past sessions matched '{query}'. "
            "Try a broader query: use OR between keywords (e.g. 'foo OR bar'), "
            "try fewer terms, or try a single keyword. "
            "Do NOT switch to knowledge_search — that searches knowledge artifacts, "
            "not session history.",
            ctx=ctx,
            count=0,
            results=[],
        )

    current_path = ctx.deps.session.session_path
    current_resolved = current_path.resolve() if current_path else None
    seen = _dedup_sessions(raw_results, current_resolved, limit)
    tasks = _prepare_tasks(seen, query)

    if not tasks:
        return tool_output(
            f"No session content could be loaded for query '{query}'.",
            ctx=ctx,
            count=0,
            results=[],
        )

    span.set_attribute("memory.summarizer.runs", len(tasks))

    coros = [
        summarize_session_around_query(
            window,
            query,
            {"session_id": sid, "when": info.created_at[:10]},
            ctx.deps,
        )
        for sid, info, window in tasks
    ]

    try:
        async with asyncio.timeout(60):
            summaries = await asyncio.gather(*coros, return_exceptions=True)
    except TimeoutError:
        span.set_attribute("memory.summarizer.timed_out", True)
        span.set_attribute("memory.summarizer.failures", len(tasks))
        return tool_output(
            "Session summarization timed out — narrow the query or reduce limit.",
            ctx=ctx,
            count=0,
            results=[],
        )

    span.set_attribute("memory.summarizer.timed_out", False)

    results_payload, failure_count = _build_results_payload(tasks, list(summaries))
    span.set_attribute("memory.summarizer.failures", failure_count)

    display_lines = [f"Found {len(results_payload)} session(s) matching '{query}':\n"]
    for idx, entry in enumerate(results_payload, 1):
        summary_preview = entry["summary"]
        if len(summary_preview) > 500:
            summary_preview = summary_preview[:497] + "..."
        display_lines.append(
            f"{idx}. [{entry['when']}] {entry['session_id']}\n   {summary_preview}"
        )

    return tool_output(
        "\n\n".join(display_lines),
        ctx=ctx,
        count=len(results_payload),
        results=results_payload,
    )
