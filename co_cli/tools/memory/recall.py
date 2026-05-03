"""Memory tools — unified recall over knowledge artifacts (including canon), session transcripts."""

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
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)

_SESSIONS_CHANNEL_CAP = 3
"""Maximum number of unique sessions returned by the sessions channel."""

_ARTIFACTS_CANON_CAP = 3
"""Maximum canon hits per memory_search call."""

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
            "channel": "sessions",
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
            "channel": "artifacts",
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
            "channel": "artifacts",
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
    """BM25 FTS / grep fallback over knowledge artifacts and canon. Returns channel='artifacts' dicts.

    Three-pass structure (FTS path): (1) canon priority pass — full body inline, capped at
    _ARTIFACTS_CANON_CAP; (2) user priority pass — capped at _ARTIFACTS_USER_CAP;
    (3) waterfall pass (rule / article / note) — dual-capped by count
    (_ARTIFACTS_WATERFALL_CHUNK_CAP) and full-chunk content chars (_ARTIFACTS_WATERFALL_SIZE_CAP).
    Grep fallback (store=None): canon excluded — canon files are not in knowledge_dir.
    """
    store = ctx.deps.memory_store
    results: list[dict] = []

    if store is not None:
        # Canon priority pass
        if (kinds is None or "canon" in kinds) and ctx.deps.config.personality:
            try:
                canon_hits = store.search(query, sources=["canon"], limit=_ARTIFACTS_CANON_CAP)
            except Exception as e:
                logger.warning("Canon FTS search failed: %s", e)
                canon_hits = []
            for r in canon_hits:
                # real tars humor file body is ~700 chars; FTS5 snippet() would cap at ~200
                body = store.get_chunk_content("canon", r.path, 0)
                if body is None:
                    logger.warning("canon hit missing chunk content: %s", r.path)
                    continue
                results.append(
                    {
                        "channel": "artifacts",
                        "kind": "canon",
                        "title": r.title or (Path(r.path).stem if r.path else ""),
                        "snippet": body,
                        "score": r.score,
                        "path": r.path,
                        "filename_stem": Path(r.path).stem if r.path else "",
                    }
                )

        # User priority pass
        if kinds is None or "user" in kinds:
            results.extend(_user_priority_pass(store, query))

        # Waterfall pass (rule / article / note — or caller-specified non-priority kinds)
        # set subtraction avoids TypeError; empty guard prevents invalid SQL (kind IN ())
        waterfall_kinds = list(set(kinds or ["rule", "article", "note"]) - {"canon", "user"})
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
                        "channel": "artifacts",
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

    # Grep fallback (store=None): canon excluded — canon files are not in knowledge_dir
    return _grep_artifacts_fallback(ctx, query, kinds, limit)


def _grep_artifacts_fallback(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
) -> list[dict]:
    """Grep-based artifact search used when MemoryStore is unavailable. Canon silently excluded."""
    grep_kinds = [k for k in (kinds or ["user", "rule", "article", "note"]) if k != "canon"]
    if not grep_kinds:
        return []
    artifacts = load_artifacts(ctx.deps.knowledge_dir, artifact_kinds=grep_kinds)
    matches = grep_recall(artifacts, query, limit)
    return [
        {
            "channel": "artifacts",
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
                "channel": "sessions",
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

    artifact_results = [r for r in all_results if r["channel"] == "artifacts"]
    session_results = [r for r in all_results if r["channel"] == "sessions"]
    canon_artifact_results = [r for r in artifact_results if r.get("kind") == "canon"]
    non_canon_artifact_results = [r for r in artifact_results if r.get("kind") != "canon"]

    if non_canon_artifact_results:
        lines.append("**Saved artifacts:**")
        for r in non_canon_artifact_results:
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

    if canon_artifact_results:
        lines.append("\n**Character canon:**")
        for r in canon_artifact_results:
            lines.append(f"\n### {r['title']}\n{r['snippet']}")

    return "\n".join(lines)


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def memory_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    kinds: list[str] | None = None,
    limit: int = 10,
) -> ToolReturn:
    """Search memory across saved artifacts (including canon) and past sessions in one call.

    Searches the artifacts channel (BM25 FTS5/grep) and the sessions channel
    (chunk-level index, no LLM). Returns a flat list with a "channel" field per result
    ("artifacts" or "sessions"). The sessions channel is capped at 3 unique sessions
    regardless of limit.

    Canon flows through the artifacts channel as kind='canon'. Canon hits carry the full
    body inline in the snippet field (no file_read follow-up needed).

    IMPORTANT: scores in results are NOT cross-comparable across channels — use the
    "channel" field to interpret each result's provenance.

    USE THIS for ALL recall tasks — saved preferences, past conversations, project
    conventions, saved articles.

    Concrete examples:
    - "what do I prefer for testing?" → memory_search kinds=["user"]
    - "what did we figure out about docker last time?" → memory_search (searches sessions)
    - "what was that auth bug we hit?" → memory_search (searches both)
    - "what's our convention for logging?" → memory_search kinds=["rule"]

    KIND SELECTION — supply up to 3 kinds as a list for targeted recall:

      TAXONOMY:
        "user"    — everything about the user: identity, preferences, corrections, feedback
        "rule"    — prescriptive guidance: mandates, decisions with rationale, conventions
        "article" — synthesized content: analysis, summaries, research notes, saved URLs
        "note"    — catch-all; rarely worth filtering to directly
        "canon"   — read-only character scenes (full body inline)

      INTENT → KINDS:
        "what do I prefer / how do I like / who am I..."   → ["user"]
        "how do I usually handle / my approach to..."      → ["user", "rule"]
        "what do I know about / what have I saved..."      → ["article"]
        "everything about X"                               → ["user", "rule", "article"]
        broad or uncertain intent                          → omit kinds (searches all)

    Search syntax (FTS5): keywords joined with OR for broad recall (auth OR login OR session),
    phrases for exact match ("connection pool"), boolean (python NOT java), prefix (deploy*).

    Artifact hits render as `**<title>** [<kind>] @ <path>: <snippet>` — call file_read
    on the `@ <path>` value when you need the full body. Canon hits (kind='canon') render
    the full body inline; no follow-up needed. Session hits render a chunk citation and
    snippet inline; call memory_read_session_turn(session_id, start_line, end_line) for
    the verbatim turns.

    Artifacts result fields: channel, kind, title, snippet, score, path, filename_stem
    Sessions result fields:  channel, session_id, when, source, chunk_text, start_line, end_line, score

    Args:
        query: FTS5 keyword query.
        kinds: Up to 3 artifact kinds to filter results. None searches all kinds.
        limit: Max artifacts results (default 10). Sessions channel is always capped at 3.
    """
    span = otel_trace.get_current_span()

    limit = max(1, int(limit))

    if not query or not query.strip():
        session_results = _browse_recent(ctx, limit, span)
        artifact_results = _list_artifacts(ctx, kinds, limit, span)
        all_results = session_results + artifact_results
        if not all_results:
            return tool_output("No past sessions found.", ctx=ctx, count=0, results=[])
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
        return tool_output(
            "\n".join(lines),
            ctx=ctx,
            count=len(all_results),
            results=all_results,
        )

    query = query.strip()

    knowledge_results = _search_artifacts(ctx, query, kinds, limit)
    session_results_raw = _search_sessions(ctx, query, span)

    all_results: list[dict] = list(knowledge_results) + list(session_results_raw)

    if not all_results:
        return tool_output(
            f"No results found for '{query}'.",
            ctx=ctx,
            count=0,
            results=[],
        )

    return tool_output(
        _format_search_display(query, all_results),
        ctx=ctx,
        count=len(all_results),
        results=all_results,
    )
