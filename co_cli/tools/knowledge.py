"""Knowledge tools — save and list reusable knowledge artifacts.

Artifacts are stored as markdown files with YAML frontmatter in
``ctx.deps.knowledge_dir`` (default ``~/.co-cli/knowledge/``). An
``artifact_kind`` subtype distinguishes preferences, rules, feedback, articles,
references, and notes. FTS5/BM25 via ``knowledge_store`` powers search.

``save_knowledge`` is the sole write path for knowledge artifacts, exposed to
the extractor sub-agent and dream-cycle consolidation.
"""

import hashlib
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.knowledge._artifact import (
    ArtifactKindEnum,
    KnowledgeArtifact,
    SourceTypeEnum,
    load_knowledge_artifacts,
)
from co_cli.knowledge._frontmatter import (
    parse_frontmatter,
    render_frontmatter,
    render_knowledge_file,
)
from co_cli.knowledge._ranking import compute_confidence, detect_contradictions
from co_cli.knowledge._similarity import find_similar_artifacts, is_content_superset
from co_cli.tools.memory import filter_memories, grep_recall
from co_cli.tools.tool_io import tool_output, tool_output_raw

_TRACER = otel_trace.get_tracer("co.knowledge")

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug, max 50 chars."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _update_artifact_body(
    artifact: KnowledgeArtifact,
    new_body: str,
    ctx: RunContext[CoDeps],
) -> None:
    """Atomically overwrite the body of an existing artifact and re-index it."""
    raw = artifact.path.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(raw)
    fm["updated"] = datetime.now(UTC).isoformat()
    md_content = render_frontmatter(fm, new_body)
    with tempfile.NamedTemporaryFile(
        "w", dir=artifact.path.parent, suffix=".tmp", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(md_content)
    os.replace(tmp.name, artifact.path)
    if ctx.deps.knowledge_store is not None:
        _reindex_knowledge_file(ctx, artifact.path, new_body, md_content, fm, artifact.path.stem)


def _reindex_knowledge_file(
    ctx: RunContext[CoDeps],
    path: Path,
    body: str,
    md_content: str,
    fm: dict[str, Any],
    slug: str,
) -> None:
    """Re-index a knowledge file's docs row and chunk rows after in-place mutation.

    Both legs must stay in sync when the file body changes: docs_fts serves
    non-chunks queries, chunks_fts serves chunk-level queries. sync_dir normally
    handles both at once, but callers that mutate a single file (e.g. update_memory,
    append_memory, _update_artifact_body) need to refresh the DB inline.
    """
    store = ctx.deps.knowledge_store
    if store is None:
        return
    content_hash = hashlib.sha256(md_content.encode()).hexdigest()
    artifact_kind = fm.get("artifact_kind", ArtifactKindEnum.NOTE.value)
    store.index(
        source="knowledge",
        kind=artifact_kind,
        path=str(path),
        title=fm.get("title") or slug,
        content=body.strip(),
        mtime=path.stat().st_mtime,
        hash=content_hash,
        tags=" ".join(fm.get("tags", [])) or None,
        created=fm.get("created"),
        type=artifact_kind,
        description=fm.get("description"),
    )
    from co_cli.knowledge._chunker import chunk_text

    chunks = chunk_text(
        body.strip(),
        chunk_size=ctx.deps.config.knowledge.chunk_size,
        overlap=ctx.deps.config.knowledge.chunk_overlap,
    )
    store.index_chunks("knowledge", str(path), chunks)


async def list_knowledge(
    ctx: RunContext[CoDeps],
    offset: int = 0,
    limit: int = 20,
    kind: str | None = None,
) -> ToolReturn:
    """List saved knowledge artifacts with IDs, dates, tags, and one-line summaries.
    Returns one page at a time (default 20 per page).

    Covers all artifact kinds: preferences, decisions, rules, feedback, articles,
    references, and notes. For targeted lookup by keyword, use search_knowledge.
    For personal notes, use list_notes. For cloud documents, use search_drive_files.

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


async def save_knowledge(
    ctx: RunContext[CoDeps],
    content: str,
    artifact_kind: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> ToolReturn:
    """Save a reusable knowledge artifact (preference, rule, feedback, decision, article, reference, note).

    Writes a canonical kind=knowledge markdown file under ctx.deps.knowledge_dir
    and indexes it under source='knowledge' so search_knowledge can retrieve it.

    When consolidation_enabled=True, a similarity check runs before writing:
    near-identical content (>0.9 Jaccard) is skipped; overlapping content is
    merged into the existing artifact rather than creating a duplicate.

    Args:
        content: Primary text of the artifact.
        artifact_kind: One of preference | decision | rule | feedback | article | reference | note.
        title: Optional human-readable label.
        description: Optional ≤200-char hook used for retrieval ranking.
        tags: Optional retrieval labels (lowercased before writing).
    """
    valid_kinds = {e.value for e in ArtifactKindEnum}
    if artifact_kind not in valid_kinds:
        raise ValueError(
            f"Unknown artifact_kind: {artifact_kind!r}. Valid values: {sorted(valid_kinds)}"
        )

    knowledge_dir = ctx.deps.knowledge_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    if ctx.deps.config.knowledge.consolidation_enabled:
        threshold = ctx.deps.config.knowledge.consolidation_similarity_threshold
        existing = load_knowledge_artifacts(knowledge_dir, artifact_kind=artifact_kind)
        matches = find_similar_artifacts(content, artifact_kind, existing, threshold)
        if matches:
            best_artifact, best_score = matches[0]
            with _TRACER.start_as_current_span("co.knowledge.dedup") as span:
                if best_score > 0.9:
                    span.set_attribute("knowledge.dedup_action", "skipped")
                    return tool_output_raw(
                        f"Skipped (near-identical to {best_artifact.path.name})",
                        action="skipped",
                        path=str(best_artifact.path),
                        artifact_id=best_artifact.id,
                    )
                if is_content_superset(content, best_artifact.content):
                    dedup_action = "merged"
                    merged_body = content
                else:
                    dedup_action = "appended"
                    merged_body = best_artifact.content.rstrip() + "\n" + content
                span.set_attribute("knowledge.dedup_action", dedup_action)
                _update_artifact_body(best_artifact, merged_body, ctx)
                return tool_output_raw(
                    f"✓ Saved knowledge ({dedup_action}): {best_artifact.path.name}",
                    action=dedup_action,
                    path=str(best_artifact.path),
                    artifact_id=best_artifact.id,
                )

    artifact_id = str(uuid4())
    slug = _slugify(title) if title else _slugify(content[:50])
    filename = f"{slug}-{artifact_id[:8]}.md"
    file_path = knowledge_dir / filename

    session_path = ctx.deps.session.session_path
    source_ref = session_path.stem if session_path and str(session_path) else None

    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=file_path,
        artifact_kind=artifact_kind,
        title=title,
        content=content,
        created=datetime.now(UTC).isoformat(),
        description=description,
        tags=[t.lower() for t in (tags or [])],
        source_type=SourceTypeEnum.DETECTED.value,
        source_ref=source_ref,
    )

    file_content = render_knowledge_file(artifact)
    with _TRACER.start_as_current_span("co.knowledge.save") as span:
        span.set_attribute("knowledge.artifact_kind", artifact_kind)
        file_path.write_text(file_content, encoding="utf-8")

    if ctx.deps.knowledge_store is not None:
        content_hash = hashlib.sha256(file_content.encode()).hexdigest()
        store = ctx.deps.knowledge_store
        store.index(
            source="knowledge",
            kind=artifact_kind,
            path=str(file_path),
            title=title or slug,
            content=content.strip(),
            mtime=file_path.stat().st_mtime,
            hash=content_hash,
            tags=" ".join(artifact.tags) if artifact.tags else None,
            created=artifact.created,
            type=artifact_kind,
            description=description,
        )
        from co_cli.knowledge._chunker import chunk_text

        chunks = chunk_text(
            content.strip(),
            chunk_size=ctx.deps.config.knowledge.chunk_size,
            overlap=ctx.deps.config.knowledge.chunk_overlap,
        )
        store.index_chunks("knowledge", str(file_path), chunks)

    return tool_output_raw(
        f"✓ Saved knowledge: {filename}",
        action="saved",
        path=str(file_path),
        artifact_id=artifact_id,
    )


# ---------------------------------------------------------------------------
# Article tools (externally-fetched reference material)
# ---------------------------------------------------------------------------


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
    if source not in (None, "knowledge"):
        return tool_output(
            f"No results for '{query}' (source={source!r} requires FTS)",
            ctx=ctx,
            count=0,
            results=[],
        )
    otel_trace.get_current_span().set_attribute("rag.backend", "grep")
    artifacts = load_knowledge_artifacts(ctx.deps.knowledge_dir, artifact_kind=kind)
    artifacts = filter_memories(artifacts, tags, tag_match_mode, created_after, created_before)
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
    or when you want unified results across local knowledge artifacts, Obsidian
    notes, and Drive docs in a single ranked result set.

    Source filter shortcuts:
    - source="knowledge" → local knowledge artifacts only (preferences, rules, articles, notes)
    - source="obsidian"  → Obsidian vault notes only
    - source="drive"     → Google Drive docs only

    Default (source=None) searches knowledge, obsidian, and drive.

    Falls back to grep on knowledge files when FTS unavailable. Obsidian and
    Drive require FTS — results are local-artifact-only in fallback mode.

    Returns a dict with:
    - display: formatted ranked results — show directly to the user
    - count: number of results
    - results: list of {source, kind, title, snippet, score, path} dicts

    Args:
        query: Free-text search query.
        kind: Filter by artifact_kind — "preference", "article", "rule", etc. None = all.
        source: Filter by source — "knowledge", "obsidian", or "drive". None = all.
        limit: Max results to return (default 10).
        tags: Tag filter list. None = no filter.
        tag_match_mode: 'any' (OR) or 'all' (AND — doc must have every tag).
        created_after: ISO8601 date string; only return items created on or after this date.
        created_before: ISO8601 date string; only return items created on or before this date.
    """
    if ctx.deps.knowledge_store is None:
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
    fts_source = source if source is not None else ["knowledge", "obsidian", "drive"]
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
    Articles are decay-protected and persist indefinitely.

    Deduplication by origin_url: saving the same URL a second time consolidates
    (updates content and tags) rather than creating a duplicate. The origin_url
    is stored as ``source_ref`` on the artifact and serves as the dedup key.

    Returns a dict with:
    - display: confirmation message — show directly to the user
    - article_id: assigned ID
    - action: "saved" (new) or "consolidated" (merged with existing URL)

    Args:
        content: Full markdown body of the article.
        title: Article title (used in display and as slug base).
        origin_url: Source URL the article was fetched from.
        tags: Categorization tags (e.g. ["python", "async", "reference"]).
        related: Slugs of related knowledge artifacts for cross-linking.
    """
    knowledge_dir = ctx.deps.knowledge_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    existing = _find_article_by_url(knowledge_dir, origin_url)
    if existing is not None:
        return _consolidate_and_reindex(ctx, existing, content, title, tags, origin_url)

    article_id = str(uuid4())
    slug = _slugify(title[:50])
    filename = f"{slug}-{article_id[:6]}.md"
    file_path = knowledge_dir / filename

    artifact = KnowledgeArtifact(
        id=article_id,
        path=file_path,
        artifact_kind=ArtifactKindEnum.ARTICLE.value,
        title=title,
        content=content,
        created=datetime.now(UTC).isoformat(),
        tags=list(tags or []),
        related=list(related or []),
        source_type=SourceTypeEnum.WEB_FETCH.value,
        source_ref=origin_url,
        decay_protected=True,
    )
    md_content = render_knowledge_file(artifact)
    file_path.write_text(md_content, encoding="utf-8")
    logger.info(f"Saved article {article_id} to {file_path}")

    if ctx.deps.knowledge_store is not None:
        try:
            ctx.deps.knowledge_store.index(
                source="knowledge",
                kind=ArtifactKindEnum.ARTICLE.value,
                path=str(file_path),
                title=title,
                content=content,
                mtime=file_path.stat().st_mtime,
                hash=_content_hash(md_content),
                tags=" ".join(tags or []),
                created=artifact.created,
            )
            from co_cli.knowledge._chunker import chunk_text

            article_chunks = chunk_text(
                content,
                chunk_size=ctx.deps.config.knowledge.chunk_size,
                overlap=ctx.deps.config.knowledge.chunk_overlap,
            )
            ctx.deps.knowledge_store.index_chunks("knowledge", str(file_path), article_chunks)
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
            source="knowledge",
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
            fm_data: dict = {}
            if r.path:
                try:
                    raw = Path(r.path).read_text(encoding="utf-8")
                    fm_data, _ = parse_frontmatter(raw)
                except Exception:
                    pass
            article_id = fm_data.get("id")
            origin_url = fm_data.get("source_ref")
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
    Use search_knowledge for conversation-derived facts and all reusable artifacts.

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
            max_results,
        )
        if result is not None:
            return result

    return _grep_search_articles(
        ctx, knowledge_dir, query, tags, tag_match_mode, created_after, created_before, max_results
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
    knowledge_dir = ctx.deps.knowledge_dir

    # Find by slug (file stem)
    candidates = list(knowledge_dir.glob(f"{slug}.md"))
    if not candidates:
        # Try prefix match (slug might be partial)
        candidates = list(knowledge_dir.glob(f"{slug}*.md"))
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


def _find_article_by_url(knowledge_dir: Path, origin_url: str) -> Path | None:
    """Return the existing article whose ``source_ref`` matches origin_url, else None."""
    if not knowledge_dir.exists():
        return None
    for path in knowledge_dir.glob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
            if origin_url not in raw:
                continue
            fm, _ = parse_frontmatter(raw)
            if fm.get("source_ref") == origin_url:
                return path
        except Exception:
            continue
    return None


def _consolidate_and_reindex(
    ctx: RunContext[CoDeps],
    path: Path,
    new_content: str,
    new_title: str,
    new_tags: list[str] | None,
    origin_url: str,
) -> ToolReturn:
    """Consolidate an existing article (same source_ref) and re-sync the index."""
    raw = path.read_text(encoding="utf-8")
    fm, _ = parse_frontmatter(raw)

    existing_tags = fm.get("tags") or []
    merged_tags = sorted(set(existing_tags) | set(new_tags or []))
    artifact_id = str(fm.get("id") or "")
    created = fm.get("created") or datetime.now(UTC).isoformat()

    artifact = KnowledgeArtifact(
        id=artifact_id,
        path=path,
        artifact_kind=ArtifactKindEnum.ARTICLE.value,
        title=new_title,
        content=new_content,
        created=created,
        updated=datetime.now(UTC).isoformat(),
        tags=merged_tags,
        related=list(fm.get("related") or []),
        source_type=SourceTypeEnum.WEB_FETCH.value,
        source_ref=origin_url,
        decay_protected=True,
    )
    md_content = render_knowledge_file(artifact)
    path.write_text(md_content, encoding="utf-8")
    logger.info(f"Consolidated article {artifact_id} (same origin_url)")

    if ctx.deps.knowledge_store is not None:
        try:
            ctx.deps.knowledge_store.index(
                source="knowledge",
                kind=ArtifactKindEnum.ARTICLE.value,
                path=str(path),
                title=new_title,
                content=new_content.strip(),
                mtime=path.stat().st_mtime,
                hash=_content_hash(md_content),
                tags=" ".join(merged_tags),
                created=created,
                updated=artifact.updated,
            )
            from co_cli.knowledge._chunker import chunk_text

            chunks = chunk_text(
                new_content.strip(),
                chunk_size=ctx.deps.config.knowledge.chunk_size,
                overlap=ctx.deps.config.knowledge.chunk_overlap,
            )
            ctx.deps.knowledge_store.index_chunks("knowledge", str(path), chunks)
        except Exception as e:
            logger.warning(f"Failed to reindex consolidated article: {e}")

    return tool_output_raw(
        f"✓ Updated article {artifact_id}: {path.name}\nSource: {origin_url}\nLocation: {path}",
        article_id=artifact_id,
        action="consolidated",
    )


def _content_hash(content: str) -> str:
    """SHA256 hash of file content for FTS change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
