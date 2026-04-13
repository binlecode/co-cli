"""Article tools for saving and retrieving external reference material.

Articles are externally-fetched knowledge items (web docs, reference material,
research) stored as markdown files with YAML frontmatter in the user-global library
(~/.co-cli/library/ by default, configurable via CO_LIBRARY_PATH).
They differ from memories in three ways:
- kind: article (vs kind: memory)
- origin_url: URL they were fetched from
- decay_protected: true by default

Use save_article vs save_memory:
- save_article: externally-fetched web content, reference material, documentation
- save_memory: conversation-derived facts, user preferences, decisions, corrections

Use search_articles for summary-level lookup; use read_article for full body.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.knowledge._frontmatter import parse_frontmatter, validate_memory_frontmatter
from co_cli.knowledge._ranking import compute_confidence, detect_contradictions
from co_cli.tools.memory import filter_memories, grep_recall, load_memories
from co_cli.tools.tool_output import tool_output, tool_output_raw

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug (max 50 chars)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


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
    if source not in (None, "library"):
        return tool_output(
            f"No results for '{query}' (source={source!r} requires FTS)",
            ctx=ctx,
            count=0,
            results=[],
        )
    otel_trace.get_current_span().set_attribute("rag.backend", "grep")
    effective_kind = kind if kind is not None else "article"
    memories = load_memories(ctx.deps.library_dir, kind=effective_kind)
    memories = filter_memories(memories, tags, tag_match_mode, created_after, created_before)
    matches = grep_recall(memories, query, limit)
    if not matches:
        return tool_output(f"No results found for '{query}'", ctx=ctx, count=0, results=[])
    lines = [f"Found {len(matches)} result(s) for '{query}':\n"]
    result_dicts = []
    for m in matches:
        result_source = "memory" if m.kind == "memory" else "library"
        lines.append(f"**{m.path.stem}** [{m.kind}]: {m.content[:100]}")
        result_dicts.append(
            {
                "source": result_source,
                "kind": m.kind,
                "title": m.path.stem,
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


async def search_knowledge(
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
    or when you want unified results across memories, articles, Obsidian notes,
    and Drive docs in a single ranked result set.

    Source filter shortcuts:
    - source="library" → local articles only
    - source="obsidian" → Obsidian vault notes only

    Default (source=None) searches library, obsidian, and drive — memories excluded.
    Use search_memories() for dedicated memory search.

    Falls back to grep on knowledge files (articles) when FTS unavailable.
    Obsidian and Drive require FTS — results are article-only in fallback mode.

    Returns a dict with:
    - display: formatted ranked results — show directly to the user
    - count: number of results
    - results: list of {source, kind, title, snippet, score, path} dicts

    Args:
        query: Free-text search query.
        kind: Filter by kind — "memory" or "article". None = all.
        source: Filter by source — "library", "obsidian", or "drive". None = all.
        limit: Max results to return (default 10).
        tags: Tag filter list. None = no filter.
        tag_match_mode: 'any' (OR) or 'all' (AND — doc must have every tag).
        created_after: ISO8601 date string; only return items created on or after this date.
        created_before: ISO8601 date string; only return items created on or before this date.
    """
    if ctx.deps.knowledge_store is None:
        # Fallback: grep knowledge files (articles); obsidian/drive require FTS
        return _grep_fallback_knowledge(
            ctx, query, source, kind, tags, tag_match_mode, created_after, created_before, limit
        )

    if ctx.deps.obsidian_vault_path and source in (None, "obsidian"):
        try:
            ctx.deps.knowledge_store.sync_dir("obsidian", ctx.deps.obsidian_vault_path)
        except Exception as e:
            logger.warning(f"Obsidian sync failed: {e}")

    otel_trace.get_current_span().set_attribute(
        "rag.backend", ctx.deps.config.knowledge.search_backend
    )
    # Reject source="memory" — use search_memories() for memory search.
    if source == "memory":
        return tool_output(
            "source='memory' is not supported by search_knowledge. Use search_memories() instead.",
            ctx=ctx,
            count=0,
            results=[],
        )
    fts_source = source if source is not None else ["library", "obsidian", "drive"]
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
        logger.warning(f"search_knowledge FTS error: {e}")
        return tool_output(f"Search error: {e}", ctx=ctx, count=0, results=[])

    if not results:
        return tool_output(f"No results found for '{query}'", ctx=ctx, count=0, results=[])

    return _post_process_knowledge_results(ctx, query, results)


async def save_article(
    ctx: RunContext[CoDeps],
    content: str,
    title: str,
    origin_url: str,
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> ToolReturn:
    """Save an article from external reference material for long-term retrieval.

    Use for web pages, documentation, or research worth keeping permanently.
    Articles are decay-protected and persist indefinitely unlike memories.

    Use save_article for externally-fetched content — web docs, API references,
    research papers, guides. Use save_memory for conversation-derived facts
    (preferences, decisions, corrections).

    Deduplication by origin_url: saving the same URL a second time consolidates
    (updates content and tags) rather than creating a duplicate. The origin_url
    is the dedup key, not content similarity.

    Returns a dict with:
    - display: confirmation message — show directly to the user
    - article_id: assigned ID
    - action: "saved" (new) or "consolidated" (merged with existing URL)

    Args:
        content: Full markdown body of the article.
        title: Article title (used in display and as slug base).
        origin_url: Source URL the article was fetched from.
        tags: Categorization tags (e.g. ["python", "async", "reference"]).
        related: Slugs of related memories/articles for knowledge linking.
    """
    library_dir = ctx.deps.library_dir
    library_dir.mkdir(parents=True, exist_ok=True)

    # Dedup by origin_url exact match
    existing = _find_article_by_url(library_dir, origin_url)
    if existing is not None:
        result = _consolidate_article(existing, content, title, tags, origin_url)
        if ctx.deps.knowledge_store is not None:
            try:
                updated_raw = existing.read_text(encoding="utf-8")
                fm2, body2 = parse_frontmatter(updated_raw)
                # Use merged tags from frontmatter, not just the incoming tags arg
                merged_tags_str = " ".join(fm2.get("tags", []))
                ctx.deps.knowledge_store.index(
                    source="library",
                    kind="article",
                    path=str(existing),
                    title=title,
                    content=body2.strip(),
                    mtime=existing.stat().st_mtime,
                    hash=_content_hash(updated_raw),
                    tags=merged_tags_str,
                    created=fm2.get("created"),
                    updated=fm2.get("updated"),
                )
                from co_cli.knowledge._chunker import chunk_text

                consolidated_chunks = chunk_text(
                    body2.strip(),
                    chunk_size=ctx.deps.config.knowledge.chunk_size,
                    overlap=ctx.deps.config.knowledge.chunk_overlap,
                )
                ctx.deps.knowledge_store.index_chunks(
                    "library", str(existing), consolidated_chunks
                )
            except Exception as e:
                logger.warning(f"Failed to reindex consolidated article: {e}")
        return result

    import uuid as _uuid

    article_id = str(_uuid.uuid4())
    slug = _slugify(title[:50])
    filename = f"{slug}-{article_id[:6]}.md"

    frontmatter: dict[str, Any] = {
        "id": article_id,
        "kind": "article",
        "title": title,
        "origin_url": origin_url,
        "created": datetime.now(UTC).isoformat(),
        "tags": tags or [],
        "provenance": "web-fetch",
        "decay_protected": True,
        "auto_category": None,
    }
    if related:
        frontmatter["related"] = related

    validate_memory_frontmatter(frontmatter)

    md_content = (
        f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{content.strip()}\n"
    )

    file_path = library_dir / filename
    file_path.write_text(md_content, encoding="utf-8")
    logger.info(f"Saved article {article_id} to {file_path}")

    if ctx.deps.knowledge_store is not None:
        try:
            ctx.deps.knowledge_store.index(
                source="library",
                kind="article",
                path=str(file_path),
                title=title,
                content=content,
                mtime=file_path.stat().st_mtime,
                hash=_content_hash(md_content),
                tags=" ".join(tags or []),
                created=frontmatter["created"],
            )
            from co_cli.knowledge._chunker import chunk_text

            article_chunks = chunk_text(
                content,
                chunk_size=ctx.deps.config.knowledge.chunk_size,
                overlap=ctx.deps.config.knowledge.chunk_overlap,
            )
            ctx.deps.knowledge_store.index_chunks("library", str(file_path), article_chunks)
        except Exception as e:
            logger.warning(f"Failed to index article {article_id}: {e}")

    return tool_output(
        f"✓ Saved article {article_id}: {filename}\nSource: {origin_url}\nLocation: {file_path}",
        ctx=ctx,
        article_id=article_id,
        action="saved",
    )


def _fts_search_articles(
    ctx: "RunContext[CoDeps]",
    library_dir: Path,
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
            source="library",
            kind="article",
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
            fm_data: dict = {}
            if r.path:
                try:
                    raw = Path(r.path).read_text(encoding="utf-8")
                    fm_data, _ = parse_frontmatter(raw)
                except Exception:
                    pass
            article_id = fm_data.get("id")
            origin_url = fm_data.get("origin_url")
            title = r.title or fm_data.get("title", Path(r.path).stem if r.path else "")
            lines.append(f"**{title}** (score: {r.score:.3f})")
            if r.tags:
                lines.append(f"Tags: {r.tags}")
            if r.snippet:
                lines.append(f"{r.snippet}\n")
            # Normalize tags to list[str] for schema parity with grep path
            tags_list = fm_data.get("tags", []) if fm_data else (r.tags.split() if r.tags else [])
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
    library_dir: Path,
    query: str,
    tags: "list[str] | None",
    tag_match_mode: "Literal['any', 'all']",
    created_after: "str | None",
    created_before: "str | None",
    max_results: int,
) -> "ToolReturn":
    """Grep fallback search path for articles."""
    articles = load_memories(library_dir, kind="article")
    query_lower = query.lower()
    matches = [
        a
        for a in articles
        if query_lower in a.content.lower() or any(query_lower in t.lower() for t in a.tags)
    ]
    matches = filter_memories(matches, tags, tag_match_mode, created_after, created_before)
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
        raw = a.path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(raw)
        title = fm.get("title", a.path.stem)
        origin_url = fm.get("origin_url", "")
        first_para = a.content.split("\n\n")[0] if a.content else ""
        if len(first_para) > 200:
            first_para = first_para[:197] + "..."
        display_id = str(a.id)[:8] if isinstance(a.id, str) else str(a.id)
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


async def search_articles(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> ToolReturn:
    """Search saved articles by keyword and return summary index only
    (title, origin_url, tags, first paragraph). Use read_article
    to load the full body after identifying an article here.

    Use search_articles for externally-fetched reference material.
    Use search_memories for conversation-derived facts.

    Results are ranked by recency (most recently updated first).
    Use short keyword queries for best results.

    Returns a dict with:
    - display: formatted article list — show directly to the user
    - count: number of articles found
    - results: list of {article_id, title, origin_url, tags, snippet, slug} dicts

    Args:
        query: Keywords to search.
        max_results: Max results to return (default 5).
        tags: Exact tag filter list. None = no filter.
        tag_match_mode: 'any' (OR — at least one tag matches) or 'all' (AND — all tags match).
        created_after: ISO8601 date string; only return articles created on or after this date.
        created_before: ISO8601 date string; only return articles created on or before this date.
    """
    library_dir = ctx.deps.library_dir

    if (
        ctx.deps.config.knowledge.search_backend in ("fts5", "hybrid")
        and ctx.deps.knowledge_store is not None
    ):
        result = _fts_search_articles(
            ctx,
            library_dir,
            query,
            tags,
            tag_match_mode,
            created_after,
            created_before,
            max_results,
        )
        if result is not None:
            return result

    return _grep_search_articles(
        ctx, library_dir, query, tags, tag_match_mode, created_after, created_before, max_results
    )


async def read_article(
    ctx: RunContext[CoDeps],
    slug: str,
) -> ToolReturn:
    """Load the full markdown body of a saved article on demand.

    Always call search_articles first to find the slug, then call
    read_article to get the full body. This two-step approach
    keeps recall responses compact.

    Does NOT summarize — returns full content as stored.
    The slug comes from the search_articles result (e.g. "042-python-asyncio-guide").

    Returns a dict with:
    - display: full article content — show directly to the user
    - article_id: article ID
    - title: article title
    - origin_url: source URL
    - content: full markdown body

    Args:
        slug: File stem from search_articles result (e.g. "042-python-asyncio-guide").
    """
    library_dir = ctx.deps.library_dir

    # Find by slug (file stem)
    candidates = list(library_dir.glob(f"{slug}.md"))
    if not candidates:
        # Try prefix match (slug might be partial)
        candidates = list(library_dir.glob(f"{slug}*.md"))
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
    origin_url = fm.get("origin_url", "")
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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _find_article_by_url(knowledge_dir: Path, origin_url: str) -> Path | None:
    """Find an existing article file by origin_url exact match.

    Returns the Path of the matching file, or None if not found.
    """
    if not knowledge_dir.exists():
        return None
    for path in knowledge_dir.glob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
            if origin_url not in raw:
                # Fast prefilter — skip frontmatter parse if URL absent
                continue
            fm, _ = parse_frontmatter(raw)
            if fm.get("origin_url") == origin_url:
                return path
        except Exception:
            continue
    return None


def _consolidate_article(
    path: Path,
    new_content: str,
    new_title: str,
    new_tags: list[str] | None,
    origin_url: str,
) -> ToolReturn:
    """Consolidate an existing article (same origin_url) with new content."""
    raw = path.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(raw)

    existing_tags = fm.get("tags", [])
    merged_tags = list(set(existing_tags + (new_tags or [])))

    fm["updated"] = datetime.now(UTC).isoformat()
    fm["tags"] = merged_tags
    fm["title"] = new_title

    md_content = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{new_content.strip()}\n"
    path.write_text(md_content, encoding="utf-8")
    logger.info(f"Consolidated article {fm.get('id')} (same origin_url)")

    return tool_output_raw(
        f"✓ Updated article {fm.get('id')}: {path.name}\nSource: {origin_url}\nLocation: {path}",
        article_id=fm.get("id"),
        action="consolidated",
    )


def _content_hash(content: str) -> str:
    """SHA256 hash of file content for FTS change detection."""
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
