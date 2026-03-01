"""Article tools for saving and retrieving external reference material.

Articles are externally-fetched knowledge items (web docs, reference material,
research) stored as markdown files with YAML frontmatter in .co-cli/knowledge/.
They differ from memories in three ways:
- kind: article (vs kind: memory)
- origin_url: URL they were fetched from
- decay_protected: true by default

Use save_article vs save_memory:
- save_article: externally-fetched web content, reference material, documentation
- save_memory: conversation-derived facts, user preferences, decisions, corrections

Use recall_article for summary-level lookup; use read_article_detail for full body.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic_ai import RunContext

from co_cli._frontmatter import parse_frontmatter, validate_memory_frontmatter
from co_cli.deps import CoDeps
from co_cli.tools.memory import _slugify, _load_memories, _grep_recall

logger = logging.getLogger(__name__)


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
) -> dict[str, Any]:
    """Primary cross-source knowledge search — use this when the source is unknown
    or when you want unified results across memories, articles, Obsidian notes,
    and Drive docs in a single ranked result set.

    Source filter shortcuts:
    - source="memory", kind="memory" → memories only
    - kind="article" → articles only
    - source="obsidian" → Obsidian vault notes only

    Falls back to grep on knowledge files (memories + articles) when FTS unavailable.
    Obsidian and Drive require FTS — results are knowledge-only in fallback mode.

    Returns a dict with:
    - display: formatted ranked results — show directly to the user
    - count: number of results
    - results: list of {source, kind, title, snippet, score, path} dicts

    Args:
        query: Free-text search query.
        kind: Filter by kind — "memory" or "article". None = all.
        source: Filter by source — "memory", "obsidian", or "drive". None = all.
        limit: Max results to return (default 10).
        tags: Tag filter list. None = no filter.
        tag_match_mode: 'any' (OR) or 'all' (AND — doc must have every tag).
        created_after: ISO8601 date string; only return items created on or after this date.
        created_before: ISO8601 date string; only return items created on or before this date.
    """
    if ctx.deps.knowledge_index is None:
        # Fallback: grep knowledge files (memories + articles); obsidian/drive require FTS
        if source is not None and source != "memory":
            return {"display": f"No results for '{query}' (source={source!r} requires FTS)", "count": 0, "results": []}
        knowledge_dir = Path.cwd() / ".co-cli/knowledge"
        memories = _load_memories(knowledge_dir, kind=kind)
        if tags:
            if tag_match_mode == "all":
                memories = [m for m in memories if all(t in m.tags for t in tags)]
            else:
                memories = [m for m in memories if any(t in m.tags for t in tags)]
        if created_after:
            memories = [m for m in memories if m.created and m.created >= created_after]
        if created_before:
            memories = [m for m in memories if m.created and m.created <= created_before]
        matches = _grep_recall(memories, query, limit)
        if not matches:
            return {"display": f"No results found for '{query}'", "count": 0, "results": []}
        lines = [f"Found {len(matches)} result(s) for '{query}':\n"]
        result_dicts = []
        for m in matches:
            lines.append(f"**{m.path.stem}** [{m.kind}]: {m.content[:100]}")
            result_dicts.append({"source": "memory", "kind": m.kind, "title": m.path.stem,
                                  "snippet": m.content[:100], "score": 0.0, "path": str(m.path)})
        return {"display": "\n".join(lines), "count": len(matches), "results": result_dicts}

    # Sync Obsidian vault into index before searching
    if ctx.deps.obsidian_vault_path and source in (None, "obsidian"):
        try:
            ctx.deps.knowledge_index.sync_dir("obsidian", ctx.deps.obsidian_vault_path)
        except Exception as e:
            logger.warning(f"Obsidian sync failed: {e}")

    try:
        results = ctx.deps.knowledge_index.search(
            query,
            source=source,
            kind=kind,
            tags=tags,
            tag_match_mode=tag_match_mode,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
        )
    except Exception as e:
        logger.warning(f"search_knowledge FTS error: {e}")
        return {"display": f"Search error: {e}", "count": 0, "results": []}

    if not results:
        return {"display": f"No results found for '{query}'", "count": 0, "results": []}

    lines = [f"Found {len(results)} result(s) for '{query}':\n"]
    result_dicts = []
    for r in results:
        kind_label = f"[{r.kind}]" if r.kind else ""
        src_label = f"[{r.source}]" if r.source else ""
        title_str = r.title or Path(r.path).stem if r.path else "unknown"
        lines.append(f"**{title_str}** {src_label}{kind_label} (score: {r.score:.3f})")
        if r.snippet:
            lines.append(f"  {r.snippet}")
        lines.append("")
        result_dicts.append({
            "source": r.source,
            "kind": r.kind,
            "title": r.title,
            "snippet": r.snippet,
            "score": r.score,
            "path": r.path,
        })

    return {
        "display": "\n".join(lines).rstrip(),
        "count": len(results),
        "results": result_dicts,
    }


async def save_article(
    ctx: RunContext[CoDeps],
    content: str,
    title: str,
    origin_url: str,
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> dict[str, Any]:
    """Save external reference material (web page, documentation, research) as
    a knowledge article for future retrieval. Articles are decay-protected and
    persist indefinitely unlike memories.

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
    knowledge_dir = Path.cwd() / ".co-cli/knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    # Dedup by origin_url exact match
    existing = _find_article_by_url(knowledge_dir, origin_url)
    if existing is not None:
        result = _consolidate_article(existing, content, title, tags, origin_url)
        if ctx.deps.knowledge_index is not None:
            try:
                updated_raw = existing.read_text(encoding="utf-8")
                fm2, body2 = parse_frontmatter(updated_raw)
                # Use merged tags from frontmatter, not just the incoming tags arg
                merged_tags_str = " ".join(fm2.get("tags", []))
                ctx.deps.knowledge_index.index(
                    source="memory",
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
            except Exception as e:
                logger.warning(f"Failed to reindex consolidated article: {e}")
        return result

    # Load all items to determine next ID
    all_items = _load_memories(knowledge_dir)
    max_id = max((m.id for m in all_items), default=0)
    article_id = max_id + 1
    slug = _slugify(title[:50])
    filename = f"{article_id:03d}-{slug}.md"

    frontmatter: dict[str, Any] = {
        "id": article_id,
        "kind": "article",
        "title": title,
        "origin_url": origin_url,
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": tags or [],
        "provenance": "web-fetch",
        "decay_protected": True,
        "auto_category": None,
    }
    if related:
        frontmatter["related"] = related

    md_content = (
        f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n"
        f"{content.strip()}\n"
    )

    file_path = knowledge_dir / filename
    file_path.write_text(md_content, encoding="utf-8")
    logger.info(f"Saved article {article_id} to {file_path}")

    # FTS integration point — activates when Phase 1 ships
    if ctx.deps.knowledge_index is not None:
        try:
            ctx.deps.knowledge_index.index(
                source="memory",
                kind="article",
                path=str(file_path),
                title=title,
                content=content,
                mtime=file_path.stat().st_mtime,
                hash=_content_hash(md_content),
                tags=" ".join(tags or []),
                created=frontmatter["created"],
            )
        except Exception as e:
            logger.warning(f"Failed to index article {article_id}: {e}")

    return {
        "display": (
            f"✓ Saved article {article_id}: {filename}\n"
            f"Source: {origin_url}\n"
            f"Location: {file_path}"
        ),
        "article_id": article_id,
        "action": "saved",
    }


async def recall_article(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> dict[str, Any]:
    """Search saved articles by keyword and return summary index only
    (title, origin_url, tags, first paragraph). Use read_article_detail
    to load the full body after identifying an article here.

    Use recall_article for externally-fetched reference material.
    Use recall_memory for conversation-derived facts.

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
    knowledge_dir = Path.cwd() / ".co-cli/knowledge"

    # FTS path — activates when Phase 1 ships and index is available
    if ctx.deps.knowledge_index is not None and ctx.deps.knowledge_search_backend in ("fts5", "hybrid"):
        try:
            fts_results = ctx.deps.knowledge_index.search(
                query,
                source="memory",
                kind="article",
                tags=tags,
                tag_match_mode=tag_match_mode,
                created_after=created_after,
                created_before=created_before,
                limit=max_results,
            )
            if not fts_results:
                return {
                    "display": f"No articles found matching '{query}'",
                    "count": 0,
                    "results": [],
                }
            result_dicts = []
            lines = [f"Found {len(fts_results)} article(s) matching '{query}':\n"]
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
                tags_list = (
                    fm_data.get("tags", [])
                    if fm_data
                    else (r.tags.split() if r.tags else [])
                )
                result_dicts.append({
                    "article_id": article_id,
                    "title": title,
                    "origin_url": origin_url,
                    "tags": tags_list,
                    "snippet": r.snippet,
                    "slug": Path(r.path).stem if r.path else "",
                })
            return {
                "display": "\n".join(lines),
                "count": len(fts_results),
                "results": result_dicts,
            }
        except Exception as e:
            logger.warning(f"FTS search failed, falling back to grep: {e}")

    # Grep fallback
    articles = _load_memories(knowledge_dir, kind="article")
    query_lower = query.lower()
    matches = [
        a for a in articles
        if query_lower in a.content.lower()
        or any(query_lower in t.lower() for t in a.tags)
    ]
    if tags:
        if tag_match_mode == "all":
            matches = [a for a in matches if all(t in a.tags for t in tags)]
        else:
            matches = [a for a in matches if any(t in a.tags for t in tags)]
    if created_after:
        matches = [a for a in matches if a.created and a.created >= created_after]
    if created_before:
        matches = [a for a in matches if a.created and a.created <= created_before]
    matches.sort(key=lambda a: a.updated or a.created, reverse=True)
    matches = matches[:max_results]

    if not matches:
        return {
            "display": f"No articles found matching '{query}'",
            "count": 0,
            "results": [],
        }

    lines = [f"Found {len(matches)} article(s) matching '{query}':\n"]
    result_dicts = []
    for a in matches:
        # Load origin_url and title from frontmatter
        raw = a.path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(raw)
        title = fm.get("title", a.path.stem)
        origin_url = fm.get("origin_url", "")

        # Summary: first paragraph only
        first_para = a.content.split("\n\n")[0] if a.content else ""
        if len(first_para) > 200:
            first_para = first_para[:197] + "..."

        lines.append(f"**{title}** (id: {a.id})")
        if origin_url:
            lines.append(f"Source: {origin_url}")
        if a.tags:
            lines.append(f"Tags: {', '.join(a.tags)}")
        lines.append(f"{first_para}\n")

        result_dicts.append({
            "article_id": a.id,
            "title": title,
            "origin_url": origin_url,
            "tags": a.tags,
            "snippet": first_para,
            "slug": a.path.stem,
        })

    return {
        "display": "\n".join(lines),
        "count": len(matches),
        "results": result_dicts,
    }


async def read_article_detail(
    ctx: RunContext[CoDeps],
    slug: str,
) -> dict[str, Any]:
    """Load the full markdown body of a saved article on demand.

    Always call recall_article first to find the slug, then call
    read_article_detail to get the full body. This two-step approach
    keeps recall responses compact.

    Does NOT summarize — returns full content as stored.
    The slug comes from the recall_article result (e.g. "042-python-asyncio-guide").

    Returns a dict with:
    - display: full article content — show directly to the user
    - article_id: article ID
    - title: article title
    - origin_url: source URL
    - content: full markdown body

    Args:
        slug: File stem from recall_article result (e.g. "042-python-asyncio-guide").
    """
    knowledge_dir = Path.cwd() / ".co-cli/knowledge"

    # Find by slug (file stem)
    candidates = list(knowledge_dir.glob(f"{slug}.md"))
    if not candidates:
        # Try prefix match (slug might be partial)
        candidates = list(knowledge_dir.glob(f"{slug}*.md"))
    if not candidates:
        return {
            "display": f"Article '{slug}' not found.",
            "article_id": None,
            "title": None,
            "origin_url": None,
            "content": None,
        }

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

    return {
        "display": f"{header}\n\n{body.strip()}",
        "article_id": article_id,
        "title": title,
        "origin_url": origin_url,
        "content": body.strip(),
    }


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
) -> dict[str, Any]:
    """Consolidate an existing article (same origin_url) with new content."""
    raw = path.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(raw)

    existing_tags = fm.get("tags", [])
    merged_tags = list(set(existing_tags + (new_tags or [])))

    fm["updated"] = datetime.now(timezone.utc).isoformat()
    fm["tags"] = merged_tags
    fm["title"] = new_title

    md_content = (
        f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n"
        f"{new_content.strip()}\n"
    )
    path.write_text(md_content, encoding="utf-8")
    logger.info(f"Consolidated article {fm.get('id')} (same origin_url)")

    return {
        "display": (
            f"✓ Updated article {fm.get('id')}: {path.name}\n"
            f"Source: {origin_url}\n"
            f"Location: {path}"
        ),
        "article_id": fm.get("id"),
        "action": "consolidated",
    }


def _content_hash(content: str) -> str:
    """SHA256 hash of file content for FTS change detection."""
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
