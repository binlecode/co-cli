"""Knowledge read tools — search, list, and retrieve knowledge artifacts."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.knowledge._artifact import (
    ArtifactKindEnum,
    IndexSourceEnum,
    KnowledgeArtifact,
    load_knowledge_artifacts,
)
from co_cli.knowledge._frontmatter import parse_frontmatter
from co_cli.knowledge._ranking import compute_confidence, detect_contradictions
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.knowledge.helpers import _touch_recalled
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)


def grep_recall(
    artifacts: list[KnowledgeArtifact],
    query: str,
    max_results: int,
) -> list[KnowledgeArtifact]:
    """Case-insensitive substring search across content and tags.

    Sorts by recency (updated or created, newest first).
    """
    query_lower = query.lower()
    matches = [
        m
        for m in artifacts
        if query_lower in m.content.lower() or any(query_lower in t.lower() for t in m.tags)
    ]
    matches.sort(key=lambda m: m.updated or m.created, reverse=True)
    return matches[:max_results]


def filter_artifacts(
    entries: list[KnowledgeArtifact],
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> list[KnowledgeArtifact]:
    """Filter artifacts by tags and creation date range."""
    result = entries
    if tags:
        if tag_match_mode == "all":
            result = [m for m in result if all(t in m.tags for t in tags)]
        else:
            result = [m for m in result if any(t in m.tags for t in tags)]
    if created_after:
        result = [m for m in result if m.created and m.created >= created_after]
    if created_before:
        result = [m for m in result if m.created and m.created <= created_before]
    return result


async def _recall_for_context(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> ToolReturn:
    """Used by build_recall_injection to surface relevant artifacts before each model-bound segment.

    Uses FTS5/BM25 DB search via knowledge_store. Returns empty when knowledge_store
    is None (degraded mode — no crash).

    Returns a dict with:
    - display: formatted artifact list — show directly to the user
    - count: number of artifacts found
    - results: list of {path, title, snippet, tags, created, score} dicts

    Args:
        query: Keywords to search (e.g. "python testing", "database", "preference").
        max_results: Max results to return (default 5).
        tags: Exact tag filter list. None = no filter.
        tag_match_mode: 'any' (OR — at least one tag matches) or 'all' (AND — all tags match).
        created_after: ISO8601 date string; only return artifacts created on or after this date.
        created_before: ISO8601 date string; only return artifacts created on or before this date.
    """
    if ctx.deps.knowledge_store is None:
        return tool_output("", ctx=ctx, count=0, results=[])

    results = ctx.deps.knowledge_store.search(
        query,
        source=IndexSourceEnum.KNOWLEDGE,
        tags=tags,
        tag_match_mode=tag_match_mode,
        created_after=created_after,
        created_before=created_before,
        limit=max_results,
    )

    if not results:
        return tool_output(
            f"No artifacts found matching '{query}'",
            ctx=ctx,
            count=0,
            results=[],
        )

    hit_paths = [r.path for r in results]
    _recall_task = asyncio.create_task(_touch_recalled(hit_paths, ctx))
    _recall_task.add_done_callback(lambda _t: None)

    lines = [
        f"Found {len(results)} artifact{'s' if len(results) != 1 else ''} matching '{query}':\n"
    ]
    result_dicts: list[dict[str, Any]] = []
    for r in results:
        created_short = (r.created or "")[:10]
        lines.append(f"**{r.title or r.path}** (created {created_short})")
        if r.tags:
            lines.append(f"Tags: {r.tags}")
        lines.append(f"{r.snippet or ''}\n")
        result_dicts.append(
            {
                "path": r.path,
                "title": r.title,
                "snippet": r.snippet,
                "tags": r.tags,
                "created": r.created,
                "score": r.score,
            }
        )
    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(results),
        results=result_dicts,
    )


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def knowledge_list(
    ctx: RunContext[CoDeps],
    offset: int = 0,
    limit: int = 20,
    kind: str | None = None,
) -> ToolReturn:
    """List saved knowledge artifacts with IDs, dates, tags, and one-line summaries.
    Returns one page at a time (default 20 per page).

    Covers all artifact kinds: preferences, decisions, rules, feedback, articles,
    references, and notes. For targeted lookup by keyword, use knowledge_search.
    For personal notes, use obsidian_list. For cloud documents, use drive_search.

    Returns a dict with:
    - display: formatted inventory — show directly to the user
    - count: number of artifacts in this page
    - total: total number of artifacts across all pages
    - offset: starting position of this page
    - limit: page size requested
    - has_more: true if more pages exist beyond this one
    - memories: list of summary dicts with id, created, updated, artifact_kind, tags, summary

    Args:
        offset: Starting position (0-based). Example: offset=20 skips the first 20 entries.
        limit: Max entries per page (default 20).
        kind: Filter by artifact_kind (e.g. "preference", "article", "rule"). None = all.
    """
    knowledge_dir = ctx.deps.knowledge_dir
    artifacts = load_knowledge_artifacts(knowledge_dir, artifact_kind=kind)

    if not artifacts:
        no_dir = not knowledge_dir.exists()
        kind_note = f" (artifact_kind={kind})" if kind else ""
        msg = "No memories saved yet." if no_dir else f"No memories found{kind_note}."
        return tool_output(
            msg,
            ctx=ctx,
            count=0,
            total=0,
            offset=offset,
            limit=limit,
            has_more=False,
            memories=[],
        )

    artifacts.sort(key=lambda a: a.id)
    total = len(artifacts)
    page = artifacts[offset : offset + limit]

    memory_dicts: list[dict[str, Any]] = []
    for a in page:
        body_lines = a.content.split("\n")
        summary = body_lines[0] if body_lines else "(empty)"
        if len(summary) > 80:
            summary = summary[:77] + "..."
        memory_dicts.append(
            {
                "id": a.id,
                "artifact_kind": a.artifact_kind,
                "created": a.created,
                "updated": a.updated,
                "tags": a.tags,
                "summary": summary,
            }
        )

    has_more = offset + limit < total
    lines = [f"Total knowledge artifacts: {total}\n"]
    for md in memory_dicts:
        created_date = md["created"][:10]
        date_str = f"{created_date} → {md['updated'][:10]}" if md.get("updated") else created_date
        kind_str = f" [{md['artifact_kind']}]"
        display_id = md["id"][:8]
        lines.append(f"**{display_id}** ({date_str}){kind_str} : {md['summary']}")

    if has_more:
        lines.append(
            f"\nShowing {offset + 1}\u2013{offset + len(page)} of {total}. "
            f"More available \u2014 call with offset={offset + limit}."
        )

    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(page),
        total=total,
        offset=offset,
        limit=limit,
        has_more=has_more,
        memories=memory_dicts,
    )


def _grep_fallback_knowledge(
    ctx: "RunContext[CoDeps]",
    query: str,
    source: str | None,
    kind: str | None,
    tags: list[str] | None,
    tag_match_mode: "Literal['any', 'all']",
    created_after: str | None,
    created_before: str | None,
    limit: int,
) -> "ToolReturn":
    """Grep-based fallback when FTS store is unavailable."""
    if source not in (None, IndexSourceEnum.KNOWLEDGE):
        return tool_output(
            f"No results for '{query}' (source={source!r} requires FTS)",
            ctx=ctx,
            count=0,
            results=[],
        )
    otel_trace.get_current_span().set_attribute("rag.backend", "grep")
    artifacts = load_knowledge_artifacts(ctx.deps.knowledge_dir, artifact_kind=kind)
    artifacts = filter_artifacts(artifacts, tags, tag_match_mode, created_after, created_before)
    matches = grep_recall(artifacts, query, limit)
    if not matches:
        return tool_output(f"No results found for '{query}'", ctx=ctx, count=0, results=[])
    lines = [f"Found {len(matches)} result(s) for '{query}':\n"]
    result_dicts = []
    for m in matches:
        lines.append(f"**{m.path.stem}** [{m.artifact_kind}]: {m.content[:100]}")
        result_dicts.append(
            {
                "source": "knowledge",
                "kind": m.artifact_kind,
                "title": m.title or m.path.stem,
                "snippet": m.content[:100],
                "score": 0.0,
                "path": str(m.path),
            }
        )
    return tool_output("\n".join(lines), ctx=ctx, count=len(matches), results=result_dicts)


def _post_process_knowledge_results(
    ctx: "RunContext[CoDeps]",
    query: str,
    results: list,
) -> "ToolReturn":
    """Compute confidence, detect contradictions, and format FTS results."""
    half_life_days = ctx.deps.config.memory.recall_half_life_days or 0
    for r in results:
        r.confidence = compute_confidence(
            r.path, r.score, r.created, r.provenance, r.certainty, half_life_days
        )
    conflict_paths = detect_contradictions(results)
    lines = [f"Found {len(results)} result(s) for '{query}':\n"]
    result_dicts = []
    for r in results:
        kind_label = f"[{r.kind}]" if r.kind else ""
        src_label = f"[{r.source}]" if r.source else ""
        title_str = r.title or Path(r.path).stem if r.path else "unknown"
        conf_str = f", conf: {r.confidence:.3f}" if r.confidence is not None else ""
        display_title = (
            f"⚠ Conflict: **{title_str}**" if r.path in conflict_paths else f"**{title_str}**"
        )
        lines.append(f"{display_title} {src_label}{kind_label} (score: {r.score:.3f}{conf_str})")
        if r.snippet:
            lines.append(f"  {r.snippet}")
        lines.append("")
        result_dicts.append(r.to_tool_output(conflict=r.path in conflict_paths))
    return tool_output(
        "\n".join(lines).rstrip(),
        ctx=ctx,
        count=len(results),
        results=result_dicts,
    )


def _fts_search_articles(
    ctx: "RunContext[CoDeps]",
    knowledge_dir: Path,
    query: str,
    tags: "list[str] | None",
    tag_match_mode: "Literal['any', 'all']",
    created_after: "str | None",
    created_before: "str | None",
    max_results: int,
) -> "ToolReturn | None":
    """FTS5 search path for articles. Returns ToolReturn on success, None to fall through."""
    try:
        fts_results = ctx.deps.knowledge_store.search(
            query,
            source=IndexSourceEnum.KNOWLEDGE,
            kind=ArtifactKindEnum.ARTICLE.value,
            tags=tags,
            tag_match_mode=tag_match_mode,
            created_after=created_after,
            created_before=created_before,
            limit=max_results,
        )
        if not fts_results:
            return tool_output(
                f"No articles found matching '{query}'",
                ctx=ctx,
                count=0,
                results=[],
            )
        lines = [f"Found {len(fts_results)} article(s) matching '{query}':\n"]
        result_dicts = []
        for r in fts_results:
            article_id = r.artifact_id
            origin_url = r.source_ref or ""
            title = r.title or (Path(r.path).stem if r.path else "")
            lines.append(f"**{title}** (score: {r.score:.3f})")
            if r.tags:
                lines.append(f"Tags: {r.tags}")
            if r.snippet:
                lines.append(f"{r.snippet}\n")
            tags_list = r.tags.split() if r.tags else []
            result_dicts.append(
                {
                    "article_id": article_id,
                    "title": title,
                    "origin_url": origin_url,
                    "tags": tags_list,
                    "snippet": r.snippet,
                    "slug": Path(r.path).stem if r.path else "",
                }
            )
        return tool_output(
            "\n".join(lines),
            ctx=ctx,
            count=len(fts_results),
            results=result_dicts,
        )
    except Exception as e:
        logger.warning(f"FTS search failed, falling back to grep: {e}")
        return None


def _grep_search_articles(
    ctx: "RunContext[CoDeps]",
    knowledge_dir: Path,
    query: str,
    tags: "list[str] | None",
    tag_match_mode: "Literal['any', 'all']",
    created_after: "str | None",
    created_before: "str | None",
    max_results: int,
) -> "ToolReturn":
    """Grep fallback search path for articles."""
    articles = load_knowledge_artifacts(
        knowledge_dir, artifact_kind=ArtifactKindEnum.ARTICLE.value
    )
    query_lower = query.lower()
    matches = [
        a
        for a in articles
        if query_lower in a.content.lower() or any(query_lower in t.lower() for t in a.tags)
    ]
    matches = filter_artifacts(matches, tags, tag_match_mode, created_after, created_before)
    matches.sort(key=lambda a: a.updated or a.created, reverse=True)
    matches = matches[:max_results]
    if not matches:
        return tool_output(
            f"No articles found matching '{query}'",
            ctx=ctx,
            count=0,
            results=[],
        )
    lines = [f"Found {len(matches)} article(s) matching '{query}':\n"]
    result_dicts = []
    for a in matches:
        title = a.title or a.path.stem
        origin_url = a.source_ref or ""
        first_para = a.content.split("\n\n")[0] if a.content else ""
        if len(first_para) > 200:
            first_para = first_para[:197] + "..."
        display_id = str(a.id)[:8]
        lines.append(f"**{title}** (id: {display_id})")
        if origin_url:
            lines.append(f"Source: {origin_url}")
        if a.tags:
            lines.append(f"Tags: {', '.join(a.tags)}")
        lines.append(f"{first_para}\n")
        result_dicts.append(
            {
                "article_id": a.id,
                "title": title,
                "origin_url": origin_url,
                "tags": a.tags,
                "snippet": first_para,
                "slug": a.path.stem,
            }
        )
    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(matches),
        results=result_dicts,
    )


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def knowledge_search(
    ctx: RunContext[CoDeps],
    query: str,
    *,
    kind: str | None = None,
    source: str | None = None,
    limit: int = 10,
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> ToolReturn:
    """Primary cross-source knowledge search — use this when the source is unknown
    or when you want unified results across local knowledge artifacts, Obsidian
    notes, and Drive docs in a single ranked result set.

    Source filter shortcuts:
    - source="knowledge" → local knowledge artifacts only (preferences, rules, articles, notes)
    - source="obsidian"  → Obsidian vault notes only
    - source="drive"     → Google Drive docs only

    Default (source=None) searches knowledge, obsidian, and drive.

    Falls back to grep on knowledge files when FTS unavailable. Obsidian and
    Drive require FTS — results are local-artifact-only in fallback mode.

    Article-index mode: when kind="article" is requested, returns the continuation
    schema needed by knowledge_article_read() — {article_id, title, origin_url, tags, snippet,
    slug} — instead of the generic cross-source schema. Use this to discover articles
    before calling knowledge_article_read(slug=...).

    Returns a dict with:
    - display: formatted ranked results — show directly to the user
    - count: number of results
    - results: list of {source, kind, title, snippet, score, path} dicts (generic),
               OR list of {article_id, title, origin_url, tags, snippet, slug} dicts
               when kind="article"

    Args:
        query: Free-text search query.
        kind: Filter by artifact_kind — "preference", "article", "rule", etc. None = all.
              When kind="article", returns article-index continuation schema for knowledge_article_read().
        source: Filter by source — "knowledge", "obsidian", or "drive". None = all.
        limit: Max results to return (default 10).
        tags: Tag filter list. None = no filter.
        tag_match_mode: 'any' (OR) or 'all' (AND — doc must have every tag).
        created_after: ISO8601 date string; only return items created on or after this date.
        created_before: ISO8601 date string; only return items created on or before this date.
    """
    # Article-index fast-path: returns continuation schema for read_article()
    if kind == "article":
        knowledge_dir = ctx.deps.knowledge_dir
        if (
            ctx.deps.config.knowledge.search_backend in ("fts5", "hybrid")
            and ctx.deps.knowledge_store is not None
        ):
            result = _fts_search_articles(
                ctx,
                knowledge_dir,
                query,
                tags,
                tag_match_mode,
                created_after,
                created_before,
                limit,
            )
            if result is not None:
                return result
        return _grep_search_articles(
            ctx, knowledge_dir, query, tags, tag_match_mode, created_after, created_before, limit
        )

    if ctx.deps.knowledge_store is None:
        return _grep_fallback_knowledge(
            ctx, query, source, kind, tags, tag_match_mode, created_after, created_before, limit
        )

    if ctx.deps.obsidian_vault_path and source in (None, IndexSourceEnum.OBSIDIAN):
        try:
            ctx.deps.knowledge_store.sync_dir(
                IndexSourceEnum.OBSIDIAN, ctx.deps.obsidian_vault_path
            )
        except Exception as e:
            logger.warning(f"Obsidian sync failed: {e}")

    otel_trace.get_current_span().set_attribute(
        "rag.backend", ctx.deps.config.knowledge.search_backend
    )
    fts_source = source if source is not None else list(IndexSourceEnum)
    try:
        results = ctx.deps.knowledge_store.search(
            query,
            source=fts_source,
            kind=kind,
            tags=tags,
            tag_match_mode=tag_match_mode,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except Exception as e:
        logger.warning(f"knowledge_search FTS error: {e}")
        return tool_output(f"Search error: {e}", ctx=ctx, count=0, results=[])

    if not results:
        return tool_output(f"No results found for '{query}'", ctx=ctx, count=0, results=[])

    return _post_process_knowledge_results(ctx, query, results)


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def knowledge_article_read(
    ctx: RunContext[CoDeps],
    slug: str,
) -> ToolReturn:
    """Load the full markdown body of a saved article on demand.

    Always call knowledge_search(query=..., kind="article", source="knowledge")
    first to find the slug, then call knowledge_article_read to get the full body. This
    two-step approach keeps recall responses compact.

    Does NOT summarize — returns full content as stored.
    The slug comes from the knowledge_search(kind="article") result
    (e.g. "042-python-asyncio-guide").

    Returns a dict with:
    - display: full article content — show directly to the user
    - article_id: article ID
    - title: article title
    - origin_url: source URL
    - content: full markdown body

    Args:
        slug: File stem from knowledge_search(kind="article") result
              (e.g. "042-python-asyncio-guide").
    """
    knowledge_dir = ctx.deps.knowledge_dir

    # Single glob — exact stem match takes priority over prefix match
    all_candidates = list(knowledge_dir.glob(f"{slug}*.md"))
    candidates = [p for p in all_candidates if p.stem == slug] or all_candidates
    if not candidates:
        return tool_output(
            f"Article '{slug}' not found.",
            ctx=ctx,
            article_id=None,
            title=None,
            origin_url=None,
            content=None,
        )

    path = candidates[0]
    raw = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(raw)

    title = fm.get("title", path.stem)
    origin_url = fm.get("source_ref") or ""
    article_id = fm.get("id")

    header_parts = [f"# {title}"]
    if origin_url:
        header_parts.append(f"Source: {origin_url}")
    header = "\n".join(header_parts)

    return tool_output(
        f"{header}\n\n{body.strip()}",
        ctx=ctx,
        article_id=article_id,
        title=title,
        origin_url=origin_url,
        content=body.strip(),
    )
