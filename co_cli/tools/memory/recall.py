"""Memory tools — unified recall over knowledge artifacts and session transcripts.

Two channels: session (past transcripts) and knowledge (declarative artifacts).
Skills are their own surface (skill_search). Canon is doctrine, auto-injected
into the static prompt by the personality system — never returned by recall.
"""

import logging
from pathlib import Path
from typing import Any

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.artifact import load_artifacts
from co_cli.memory.session_browser import list_sessions
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.memory.read import grep_recall
from co_cli.tools.tool_io import tool_error, tool_output

logger = logging.getLogger(__name__)

_SESSIONS_CHANNEL_CAP = 3
"""Maximum number of unique sessions returned by the session channel."""

_ARTIFACTS_USER_CAP = 3
"""Maximum user-kind chunk results per memory_search call."""

_ARTIFACTS_WATERFALL_CHUNK_CAP = 5
"""Maximum waterfall-pass chunk count (count cap)."""

_ARTIFACTS_WATERFALL_SIZE_CAP = 2000
"""Maximum cumulative full-chunk content chars across waterfall results (size cap)."""

# user-facing snippet truncation in tool output
_SNIPPET_DISPLAY_CHARS = 100
"""Maximum chars shown from an artifact or session chunk snippet in formatted output."""


def _browse_recent(
    ctx: RunContext[CoDeps],
    limit: int,
    span: Span,
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
            "channel": "session",
            "session_id": s.session_id,
            "when": s.created_at.isoformat()[:10],
            "title": s.title,
            "file_size": s.file_size,
        }
        for s in sessions
    ]


def _list_artifacts(
    ctx: RunContext[CoDeps],
    kinds: list[str] | None,
    limit: int,
    span: Span,
) -> list[dict]:
    """Paginated inventory of knowledge artifacts, sorted by created descending."""
    if ctx.deps.memory_store is not None:
        results = ctx.deps.memory_store.list_artifacts(kinds, limit)
        span.set_attribute("memory.artifacts.count", len(results))
        return results
    artifacts = load_artifacts(ctx.deps.knowledge_dir, artifact_kinds=kinds)
    artifacts.sort(key=lambda a: a.created, reverse=True)
    page = artifacts[:limit]
    span.set_attribute("memory.artifacts.count", len(page))
    return [
        {
            "channel": "knowledge",
            "kind": a.artifact_kind,
            "title": a.title or a.path.stem,
            "snippet": a.content[:_SNIPPET_DISPLAY_CHARS],
            "score": 0.0,
            "path": str(a.path),
            "filename_stem": a.path.stem,
        }
        for a in page
    ]


def _user_priority_pass(store: Any, query: str) -> list[dict]:
    """User priority pass — BM25 search over user-kind artifacts, capped at _ARTIFACTS_USER_CAP."""
    try:
        user_hits = store.search(
            query,
            sources=["knowledge"],
            kinds=["user"],
            limit=_ARTIFACTS_USER_CAP,
        )
    except Exception as e:
        logger.warning("User artifacts FTS search failed: %s", e)
        user_hits = []
    return [
        {
            "channel": "knowledge",
            "kind": r.kind,
            "title": r.title or (Path(r.path).stem if r.path else ""),
            "snippet": r.snippet,
            "score": r.score,
            "path": r.path,
            "filename_stem": Path(r.path).stem if r.path else "",
        }
        for r in user_hits
    ]


def _search_artifacts(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
) -> list[dict]:
    """BM25 FTS / grep fallback over knowledge artifacts. Returns channel='knowledge' dicts.

    Two-pass structure (FTS path): (1) user priority pass — capped at _ARTIFACTS_USER_CAP;
    (2) waterfall pass (rule / article / note) — dual-capped by count
    (_ARTIFACTS_WATERFALL_CHUNK_CAP) and full-chunk content chars (_ARTIFACTS_WATERFALL_SIZE_CAP).
    Canon is doctrine, auto-injected via the personality system; never surfaced here.
    """
    store = ctx.deps.memory_store
    results: list[dict] = []

    if store is not None:
        # User priority pass
        if kinds is None or "user" in kinds:
            results.extend(_user_priority_pass(store, query))

        # Waterfall pass (rule / article / note — or caller-specified non-priority kinds)
        # set subtraction avoids TypeError; empty guard prevents invalid SQL (kind IN ())
        waterfall_kinds = list(set(kinds or ["rule", "article", "note"]) - {"user"})
        if waterfall_kinds:
            try:
                candidates = store.search(
                    query,
                    sources=["knowledge"],
                    kinds=waterfall_kinds,
                    limit=_ARTIFACTS_WATERFALL_CHUNK_CAP,
                )
            except Exception as e:
                logger.warning("Waterfall artifacts FTS search failed: %s", e)
                candidates = []
            total_chars = 0
            for r in candidates:
                if total_chars >= _ARTIFACTS_WATERFALL_SIZE_CAP:
                    break
                full_content = store.get_chunk_content(r.source, r.path, r.chunk_index or 0) or ""
                results.append(
                    {
                        "channel": "knowledge",
                        "kind": r.kind,
                        "title": r.title or (Path(r.path).stem if r.path else ""),
                        "snippet": r.snippet,
                        "score": r.score,
                        "path": r.path,
                        "filename_stem": Path(r.path).stem if r.path else "",
                    }
                )
                total_chars += len(full_content)

        return results

    return _grep_artifacts_fallback(ctx, query, kinds, limit)


def _grep_artifacts_fallback(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
) -> list[dict]:
    """Grep-based artifact search used when MemoryStore is unavailable."""
    grep_kinds = list(kinds or ["user", "rule", "article", "note"])
    if not grep_kinds:
        return []
    artifacts = load_artifacts(ctx.deps.knowledge_dir, artifact_kinds=grep_kinds)
    matches = grep_recall(artifacts, query, limit)
    return [
        {
            "channel": "knowledge",
            "kind": m.artifact_kind,
            "title": m.title or m.path.stem,
            "snippet": m.content[:_SNIPPET_DISPLAY_CHARS],
            "score": 0.0,
            "path": str(m.path),
            "filename_stem": m.path.stem,
        }
        for m in matches
    ]


def _search_sessions(
    ctx: RunContext[CoDeps],
    query: str,
    span: Span,
) -> list[dict]:
    """Chunked recall over session transcripts via MemoryStore.search(sources=["session"]).

    Returns channel='sessions' dicts with chunk citation fields (chunk_text, start_line,
    end_line, score). Capped at _SESSIONS_CHANNEL_CAP unique sessions; no LLM calls.
    Returns [] when memory_store is unavailable.
    """
    from co_cli.memory.session import parse_session_filename

    store = ctx.deps.memory_store
    if store is None:
        span.set_attribute("memory.sessions.count", 0)
        return []

    try:
        raw = store.search(query, sources=["session"], limit=_SESSIONS_CHANNEL_CAP * 5)
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
                "channel": "session",
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


def _format_search_display(query: str, all_results: list[dict]) -> str:
    """Build the display string for a non-empty search result set."""
    lines: list[str] = [f"Found {len(all_results)} result(s) for '{query}':\n"]

    knowledge_results = [r for r in all_results if r["channel"] == "knowledge"]
    session_results = [r for r in all_results if r["channel"] == "session"]

    if knowledge_results:
        lines.append("**Saved artifacts:**")
        for r in knowledge_results:
            kind_str = f" [{r['kind']}]" if r.get("kind") else ""
            path_str = f" @ {r['path']}" if r.get("path") else ""
            lines.append(
                f"  **{r['title']}**{kind_str}{path_str}: "
                f"{(r.get('snippet') or '')[:_SNIPPET_DISPLAY_CHARS]}"
            )

    if session_results:
        lines.append("\n**Past sessions:**")
        for idx, entry in enumerate(session_results, 1):
            start = entry.get("start_line")
            end = entry.get("end_line")
            loc = f" @ L{start}-{end}" if start is not None and end is not None else ""
            lines.append(f"  {idx}. [{entry['when']}] {entry['session_id']}{loc}")
            preview = (entry.get("chunk_text") or "")[:_SNIPPET_DISPLAY_CHARS]
            if preview:
                lines.append(f"     {preview}")

    return "\n".join(lines)


def _format_browse_display(
    session_results: list[dict],
    artifact_results: list[dict],
) -> str:
    """Build the display string for an empty-query browse across session + knowledge channels."""
    lines: list[str] = []
    if session_results:
        lines.append(f"Recent {len(session_results)} session(s):\n")
        for idx, s in enumerate(session_results, 1):
            lines.append(f"{idx}. [{s['when']}] {s['session_id']} — {s['title']}")
    if artifact_results:
        lines.append("\n**Knowledge artifacts:**")
        for r in artifact_results:
            kind_str = f" [{r['kind']}]" if r.get("kind") else ""
            path_str = f" @ {r['path']}" if r.get("path") else ""
            lines.append(
                f"  **{r['title']}**{kind_str}{path_str}: "
                f"{(r.get('snippet') or '')[:_SNIPPET_DISPLAY_CHARS]}"
            )
    return "\n".join(lines)


def _dispatch_session_channel(
    ctx: RunContext[CoDeps],
    query: str,
    limit: int,
    span: Any,
) -> ToolReturn:
    """Handle memory_search when channel='session'."""
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
        _format_search_display(query, session_results_raw),
        ctx=ctx,
        count=len(session_results_raw),
        results=session_results_raw,
    )


def _dispatch_knowledge_channel(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
    span: Any,
) -> ToolReturn:
    """Handle memory_search when channel='knowledge'."""
    if not query:
        artifact_results = _list_artifacts(ctx, kinds, limit, span)
        if not artifact_results:
            return tool_output("No artifacts found.", ctx=ctx, count=0, results=[])
        lines: list[str] = ["\n**Knowledge artifacts:**"]
        for r in artifact_results:
            kind_str = f" [{r['kind']}]" if r.get("kind") else ""
            path_str = f" @ {r['path']}" if r.get("path") else ""
            lines.append(
                f"  **{r['title']}**{kind_str}{path_str}: {(r.get('snippet') or '')[:_SNIPPET_DISPLAY_CHARS]}"
            )
        return tool_output(
            "\n".join(lines), ctx=ctx, count=len(artifact_results), results=artifact_results
        )
    knowledge_results = _search_artifacts(ctx, query, kinds, limit)
    if not knowledge_results:
        return tool_output(f"No results found for '{query}'.", ctx=ctx, count=0, results=[])
    return tool_output(
        _format_search_display(query, knowledge_results),
        ctx=ctx,
        count=len(knowledge_results),
        results=knowledge_results,
    )


def _dispatch_all_channels(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
    span: Any,
) -> ToolReturn:
    """Handle memory_search when channel=None (session + knowledge)."""
    if not query:
        session_results = _browse_recent(ctx, limit, span)
        artifact_results = _list_artifacts(ctx, kinds, limit, span)
        all_results = session_results + artifact_results
        if not all_results:
            return tool_output("No past sessions found.", ctx=ctx, count=0, results=[])
        return tool_output(
            _format_browse_display(session_results, artifact_results),
            ctx=ctx,
            count=len(all_results),
            results=all_results,
        )

    knowledge_results = _search_artifacts(ctx, query, kinds, limit)
    session_results_raw = _search_sessions(ctx, query, span)
    all_results: list[dict] = list(knowledge_results) + list(session_results_raw)
    if not all_results:
        return tool_output(f"No results found for '{query}'.", ctx=ctx, count=0, results=[])
    return tool_output(
        _format_search_display(query, all_results),
        ctx=ctx,
        count=len(all_results),
        results=all_results,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
    delegation=frozenset({"knowledge_analyze"}),
)
async def memory_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    channel: str | None = None,
    kinds: list[str] | None = None,
    limit: int = 10,
) -> ToolReturn:
    """Search memory across saved knowledge artifacts and past session transcripts.

    Two channels: knowledge (BM25 FTS5/grep over declarative artifacts) and session
    (chunk-level transcript index, no LLM). Returns a flat list with a "channel" field
    per result ("knowledge" or "session"). The session channel is capped at 3 unique
    sessions.

    Skills are a separate surface — use skill_search to discover them.
    Canon is auto-injected via the personality system; not queryable here.

    IMPORTANT: scores in results are NOT cross-comparable across channels — use the
    "channel" field to interpret each result's provenance.

    USE THIS for recall tasks — saved preferences, past conversations, project
    conventions, saved articles.

    Concrete examples:
    - "what do I prefer for testing?" → memory_search kinds=["user"]
    - "what did we figure out about docker last time?" → memory_search (searches sessions)
    - "what was that auth bug we hit?" → memory_search (searches both)
    - "what's our convention for logging?" → memory_search kinds=["rule"]

    CHANNEL SELECTION — narrow to a single channel when intent is clear:
      "knowledge" — saved knowledge artifacts only (user, rule, article, note)
      "session"   — past session transcripts only
      None        — search both channels (default)

    KIND SELECTION — supply up to 3 kinds as a list for targeted recall (knowledge channel only):

      TAXONOMY:
        "user"    — everything about the user: identity, preferences, corrections, feedback
        "rule"    — prescriptive guidance: mandates, decisions with rationale, conventions
        "article" — synthesized content: analysis, summaries, research notes, saved URLs
        "note"    — catch-all; rarely worth filtering to directly

      INTENT → KINDS:
        "what do I prefer / how do I like / who am I..."   → ["user"]
        "how do I usually handle / my approach to..."      → ["user", "rule"]
        "what do I know about / what have I saved..."      → ["article"]
        "everything about X"                               → ["user", "rule", "article"]
        broad or uncertain intent                          → omit kinds (searches all)

    Search syntax (FTS5): keywords joined with OR for broad recall (auth OR login OR session),
    phrases for exact match ("connection pool"), boolean (python NOT java), prefix (deploy*).

    Knowledge hits render as `**<title>** [<kind>] @ <path>: <snippet>` — call file_read
    on the `@ <path>` value when you need the full body. Session hits render a chunk
    citation and snippet inline; call memory_read_session_turn(session_id, start_line,
    end_line) for the verbatim turns.

    Knowledge result fields: channel, kind, title, snippet, score, path, filename_stem
    Session result fields:   channel, session_id, when, source, chunk_text, start_line, end_line, score

    Args:
        query: FTS5 keyword query.
        channel: Restrict results to one channel. None searches both channels.
        kinds: Up to 3 artifact kinds to filter results. None searches all kinds.
        limit: Max results (default 10). Session channel is always capped at 3.
    """
    span = otel_trace.get_current_span()
    limit = max(1, int(limit))
    query = query.strip() if query else ""

    # Removed channels: skills and canon. Skills moved to skill_search; canon is doctrine.
    if channel == "skills":
        return tool_error(
            "channel='skills' is no longer supported — use skill_search instead.",
            ctx=ctx,
        )
    if channel == "canon":
        return tool_error(
            "Canon is identity, not memory — it is auto-injected via personality. Not queryable.",
            ctx=ctx,
        )

    if channel is None:
        return _dispatch_all_channels(ctx, query, kinds, limit, span)
    if channel == "session":
        return _dispatch_session_channel(ctx, query, limit, span)
    if channel == "knowledge":
        return _dispatch_knowledge_channel(ctx, query, kinds, limit, span)
    return tool_error(
        f"Unknown channel {channel!r}. Valid values: 'session', 'knowledge', or None.",
        ctx=ctx,
    )
