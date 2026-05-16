"""Memory tools — per-surface search over knowledge artifacts and session transcripts.

Two surfaces: session (past transcripts) and knowledge (declarative artifacts).
Skills are their own surface (skill_view / skill_manage). Canon is doctrine, auto-injected
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
from co_cli.memory.artifact import KnowledgeArtifact, load_artifacts
from co_cli.memory.session_browser import list_sessions
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)

_SESSIONS_CHANNEL_CAP = 3
"""Maximum number of unique sessions returned by session_search."""

_ARTIFACTS_USER_CAP = 3
"""Maximum user-kind chunk results per knowledge_search call."""

_ARTIFACTS_WATERFALL_CHUNK_CAP = 5
"""Maximum waterfall-pass chunk count (count cap)."""

_ARTIFACTS_WATERFALL_SIZE_CAP = 2000
"""Maximum cumulative full-chunk content chars across waterfall results (size cap)."""

_SNIPPET_DISPLAY_CHARS = 100
"""Maximum chars shown from an artifact or session chunk snippet in formatted output."""


def _grep_recall(
    artifacts: list[KnowledgeArtifact],
    query: str,
    max_results: int,
) -> list[KnowledgeArtifact]:
    """Case-insensitive substring search across title and content.

    Sorts by recency (updated or created, newest first).
    """
    query_lower = query.lower()
    matches = [
        m
        for m in artifacts
        if query_lower in m.content.lower() or query_lower in (m.title or "").lower()
    ]
    matches.sort(key=lambda m: m.updated or m.created, reverse=True)
    return matches[:max_results]


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
        raw = ctx.deps.memory_store.list_artifacts(kinds, limit)
        span.set_attribute("memory.artifacts.count", len(raw))
        # Drop legacy channel discriminator — each surface tool is single-tier
        return [{k: v for k, v in r.items() if k != "channel"} for r in raw]
    artifacts = load_artifacts(ctx.deps.knowledge_dir, artifact_kinds=kinds)
    artifacts.sort(key=lambda a: a.created, reverse=True)
    page = artifacts[:limit]
    span.set_attribute("memory.artifacts.count", len(page))
    return [
        {
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
    """BM25 FTS / grep fallback over knowledge artifacts. Returns kind-typed dicts.

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
    matches = _grep_recall(artifacts, query, limit)
    return [
        {
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

    Returns chunk-cited dicts (chunk_text, start_line, end_line, score). Capped at
    _SESSIONS_CHANNEL_CAP unique sessions; no LLM calls. Returns [] when memory_store
    is unavailable.
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
    """Build the display string for session_search results."""
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


def _format_knowledge_results(query: str, results: list[dict]) -> str:
    """Build the display string for knowledge_search results."""
    lines: list[str] = [f"Found {len(results)} knowledge result(s) for '{query}':\n"]
    for r in results:
        kind_str = f" [{r['kind']}]" if r.get("kind") else ""
        path_str = f" @ {r['path']}" if r.get("path") else ""
        lines.append(
            f"  **{r['title']}**{kind_str}{path_str}: "
            f"{(r.get('snippet') or '')[:_SNIPPET_DISPLAY_CHARS]}"
        )
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
    span = otel_trace.get_current_span()
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


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
)
async def knowledge_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    kinds: list[str] | None = None,
    limit: int = 10,
) -> ToolReturn:
    """Search knowledge artifacts by keyword, or browse recent artifacts.

    USE THIS for recall of saved preferences, conventions, articles, notes — anything
    the agent has learned or saved to the knowledge store.

    Empty query → recent N artifacts (title, kind, path, snippet) — browse mode.
    Non-empty → BM25 FTS5/grep search. Load a full artifact body with knowledge_view(name).

    INTENT → KINDS:
      "what do I prefer / how do I like..."     → kinds=["user"]
      "how do I usually handle / my approach..."→ kinds=["user", "rule"]
      "what do I know about / saved article..." → kinds=["article"]
      "everything about X"                      → kinds=["user", "rule", "article"]
      broad or uncertain intent                 → omit kinds (searches all)

    Search syntax (FTS5): keywords joined with OR (auth OR login), phrases ("connection pool"),
    boolean (python NOT java), prefix (deploy*).

    Result fields: kind, title, snippet, score, path, filename_stem

    Args:
        query: FTS5 keyword query.
        kinds: Up to 3 artifact kinds to filter results. None searches all kinds.
        limit: Max results (default 10).
    """
    span = otel_trace.get_current_span()
    limit = max(1, int(limit))
    query = query.strip() if query else ""

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
        _format_knowledge_results(query, knowledge_results),
        ctx=ctx,
        count=len(knowledge_results),
        results=knowledge_results,
    )
