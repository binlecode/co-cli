"""Memory tools — unified recall over knowledge artifacts, session transcripts, and canon."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.artifact import load_knowledge_artifacts
from co_cli.memory.session_browser import list_sessions
from co_cli.memory.session_store import SessionSearchResult
from co_cli.memory.summary import (
    _format_conversation,
    _truncate_around_matches,
    summarize_session_around_query,
)
from co_cli.memory.transcript import load_transcript
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.memory._canon_recall import search_canon
from co_cli.tools.memory.read import grep_recall
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)

_SUMMARIZATION_TIMEOUT_SECS: int = 60
"""Gather budget for concurrent session summarization in memory_search.

Sized for the worst-case batch: multiple sessions, each requiring one
hermes-style LLM summarization call. 60s covers parallel invocations
on a local 35B model while still failing fast on hangs.
"""

_SESSIONS_CHANNEL_CAP = 3
"""Maximum number of sessions-channel hits to summarize regardless of limit."""

_SESSION_FALLBACK_PREVIEW_CHARS = 500
"""Raw preview length used when LLM summarization fails for a session."""

_SNIPPET_DISPLAY_CHARS = 100
"""Maximum chars shown from an artifact snippet in formatted output."""

_SESSION_SUMMARY_PREVIEW_CHARS = 300
"""Maximum chars of a session summary shown in formatted output."""


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
                "channel": "sessions",
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
            preview = (
                window[:_SESSION_FALLBACK_PREVIEW_CHARS] + "\n…[truncated]"
                if window
                else "No preview available."
            )
            summary = f"[Raw preview — summarization unavailable]\n{preview}"
        else:
            summary = summary_result
        results_payload.append(
            {
                "channel": "sessions",
                "session_id": session_id,
                "when": match_info.created_at[:10],
                "source": match_info.session_path,
                "summary": summary,
            }
        )
    return results_payload, failure_count


async def _search_artifacts(
    ctx: RunContext[CoDeps],
    query: str,
    kind: str | None,
    limit: int,
) -> list[dict]:
    """BM25 FTS / grep fallback over knowledge artifacts. Returns channel='artifacts' dicts."""
    if ctx.deps.knowledge_store is not None:
        try:
            fts_results = ctx.deps.knowledge_store.search(
                query,
                source="knowledge",
                kind=kind,
                limit=limit,
            )
            return [
                {
                    "channel": "artifacts",
                    "kind": r.kind,
                    "title": r.title or (Path(r.path).stem if r.path else ""),
                    "snippet": r.snippet,
                    "score": r.score,
                    "path": r.path,
                    "slug": Path(r.path).stem if r.path else "",
                }
                for r in fts_results
            ]
        except Exception as e:
            logger.warning("Artifacts FTS search failed, falling back to grep: %s", e)
    artifacts = load_knowledge_artifacts(ctx.deps.knowledge_dir, artifact_kind=kind)
    matches = grep_recall(artifacts, query, limit)
    return [
        {
            "channel": "artifacts",
            "kind": m.artifact_kind,
            "title": m.title or m.path.stem,
            "snippet": m.content[:_SNIPPET_DISPLAY_CHARS],
            "score": 0.0,
            "path": str(m.path),
            "slug": m.path.stem,
        }
        for m in matches
    ]


async def _search_sessions(
    ctx: RunContext[CoDeps],
    query: str,
    span: Span,
) -> list[dict]:
    """FTS search over session transcripts. Returns channel='sessions' dicts.

    Capped at _SESSIONS_CHANNEL_CAP sessions regardless of caller's limit.
    Returns [] if session_store is unavailable.
    Uses best-effort summarization: sessions that finish within the timeout are
    returned; timed-out sessions fall back to raw preview rather than dropping all.
    """
    store = ctx.deps.session_store
    if store is None:
        return []

    raw_results = store.search(query, limit=_SESSIONS_CHANNEL_CAP * 5)
    if not raw_results:
        return []

    current_path = ctx.deps.session.session_path
    current_resolved = current_path.resolve() if current_path else None
    seen = _dedup_sessions(raw_results, current_resolved, _SESSIONS_CHANNEL_CAP)
    tasks = _prepare_tasks(seen, query)
    if not tasks:
        return []

    span.set_attribute("memory.summarizer.runs", len(tasks))

    async_tasks = [
        asyncio.ensure_future(
            summarize_session_around_query(
                window,
                query,
                {"session_id": sid, "when": info.created_at[:10]},
                ctx.deps,
            )
        )
        for sid, info, window in tasks
    ]

    done, pending = await asyncio.wait(async_tasks, timeout=_SUMMARIZATION_TIMEOUT_SECS)

    timed_out = bool(pending)
    for fut in pending:
        fut.cancel()

    # Collect results in original task order; substitute None for pending/failed tasks.
    summaries: list[Any] = []
    for fut in async_tasks:
        if fut in done:
            summaries.append(fut.exception() or fut.result())
        else:
            summaries.append(None)

    span.set_attribute("memory.summarizer.timed_out", timed_out)
    results_payload, failure_count = _build_results_payload(tasks, summaries)
    span.set_attribute("memory.summarizer.failures", failure_count)
    return results_payload


async def _search_canon_channel(
    ctx: RunContext[CoDeps],
    query: str,
) -> list[dict]:
    """Token-overlap search over the active role's character memory files.

    Returns [] when no personality is configured or the query has no content tokens.
    Canon channel has its own budget from config.knowledge.character_recall_limit.
    """
    role = ctx.deps.config.personality
    if not role:
        return []
    limit = ctx.deps.config.knowledge.character_recall_limit
    return search_canon(query, role=role, limit=limit)


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def memory_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    kind: str | None = None,
    limit: int = 10,
) -> ToolReturn:
    """Search memory across saved artifacts, past sessions, and canon in one call.

    TWO MODES:
    (a) Empty query → recent-sessions browse, zero LLM cost. Use when the user asks what
        was worked on recently, what we did last time, or to browse session history.
        Artifacts are not returned for empty queries (BM25 requires search terms).
    (b) Keyword query → searches the artifacts channel (BM25 FTS5/grep), the sessions
        channel (LLM-summarized), and the canon channel in parallel. Returns a flat
        list with a "channel" field per result ("artifacts", "sessions", or "canon").
        The sessions channel is capped at 3 hits regardless of limit.

    IMPORTANT: scores in results are NOT cross-comparable across channels — use the
    "channel" field to interpret each result's provenance.

    USE THIS for ALL recall tasks — saved preferences, past conversations, project
    conventions, saved articles.

    USE THIS PROACTIVELY when:
    - The user says "we did this before", "remember when", "last time", "as I mentioned"
    - The user asks about a topic you've worked on but don't have in current context
    - The user asks "what did we do about X?", "what was my preferred Y?", "our convention for Z?"
    - The user references a project, person, decision, or concept that seems familiar
    - The user asks about your character, your background, how you typically handle a situation, or references your source material

    Concrete examples:
    - "what was my preferred test runner?" → memory_search (searches artifacts preferences)
    - "what did we figure out about docker last time?" → memory_search (searches sessions)
    - "what was that auth bug we hit?" → memory_search (searches both)
    - "what's our convention for logging?" → memory_search (searches artifacts rules/decisions)

    Search syntax (FTS5): keywords joined with OR for broad recall (auth OR login OR session),
    phrases for exact match ("connection pool"), boolean (python NOT java), prefix (deploy*).

    Artifacts result fields: channel, kind, title, snippet, score, path, slug
    Sessions result fields:  channel, session_id, when, source, summary
    Canon result fields:     channel, role, title, body, score

    Args:
        query: FTS5 keyword query. Omit or pass empty string for recent-sessions browse mode.
        kind: Filter artifacts results by artifact_kind (e.g. "preference", "article"). None = all.
        limit: Max artifacts results (default 10). Sessions channel is always capped at 3.
    """
    span = otel_trace.get_current_span()

    limit = max(1, int(limit))

    if not query or not query.strip():
        return _browse_recent(ctx, limit, span)

    query = query.strip()

    knowledge_results, session_results_raw, canon_results = await asyncio.gather(
        _search_artifacts(ctx, query, kind, limit),
        _search_sessions(ctx, query, span),
        _search_canon_channel(ctx, query),
    )

    all_results: list[dict] = (
        list(knowledge_results) + list(session_results_raw) + list(canon_results)
    )

    if not all_results:
        return tool_output(
            f"No results found for '{query}'.",
            ctx=ctx,
            count=0,
            results=[],
        )

    lines = [f"Found {len(all_results)} result(s) for '{query}':\n"]

    artifact_results = [r for r in all_results if r["channel"] == "artifacts"]
    session_results = [r for r in all_results if r["channel"] == "sessions"]
    char_canon_results = [r for r in all_results if r["channel"] == "canon"]

    if artifact_results:
        lines.append("**Saved artifacts:**")
        for r in artifact_results:
            kind_str = f" [{r['kind']}]" if r.get("kind") else ""
            lines.append(
                f"  **{r['title']}**{kind_str}: {(r.get('snippet') or '')[:_SNIPPET_DISPLAY_CHARS]}"
            )

    if session_results:
        lines.append("\n**Past sessions:**")
        for idx, entry in enumerate(session_results, 1):
            summary_preview = entry.get("summary", "")
            if len(summary_preview) > _SESSION_SUMMARY_PREVIEW_CHARS:
                summary_preview = summary_preview[: _SESSION_SUMMARY_PREVIEW_CHARS - 3] + "..."
            lines.append(
                f"  {idx}. [{entry['when']}] {entry['session_id']}\n     {summary_preview}"
            )

    if char_canon_results:
        lines.append("\n**Character canon:**")
        for r in char_canon_results:
            lines.append(f"\n### {r['title']}\n{r['body']}")

    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(all_results),
        results=all_results,
    )
