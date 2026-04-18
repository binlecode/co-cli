"""Knowledge tools — save and list reusable knowledge artifacts.

Artifacts are stored as markdown files with YAML frontmatter in
``ctx.deps.knowledge_dir`` (default ``~/.co-cli/knowledge/``). An
``artifact_kind`` subtype distinguishes preferences, rules, feedback, articles,
references, and notes. FTS5/BM25 via ``knowledge_store`` powers search.

``save_knowledge`` is the sole write path for knowledge artifacts, exposed to
the extractor sub-agent and dream-cycle consolidation.
"""

import asyncio
import logging
import re
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
    IndexSourceEnum,
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
from co_cli.knowledge.mutator import _atomic_write, _reindex_knowledge_file, _update_artifact_body
from co_cli.tools.resource_lock import ResourceBusyError
from co_cli.tools.tool_io import tool_error, tool_output

_TRACER = otel_trace.get_tracer("co.knowledge")

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug, max 50 chars."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _find_by_slug(knowledge_dir: Path, slug: str) -> Path | None:
    """Return the knowledge file whose stem matches slug, or None."""
    return next((p for p in knowledge_dir.glob("*.md") if p.stem == slug), None)


async def _touch_recalled(
    paths: list[str],
    ctx: RunContext[CoDeps],
) -> None:
    """Fire-and-forget: increment recall_count and set last_recalled on hit artifacts.

    Skips silently if the file no longer exists (race with /knowledge forget).
    Does not block the recall return path — always launched via asyncio.create_task.
    """
    now = datetime.now(UTC).isoformat()
    for path_str in paths:
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)
            fm["last_recalled"] = now
            fm["recall_count"] = int(fm.get("recall_count") or 0) + 1
            md_content = render_frontmatter(fm, body)
            _atomic_write(path, md_content)
            if ctx.deps.knowledge_store is not None:
                _reindex_knowledge_file(ctx, path, body, md_content, fm, path.stem)
        except Exception:
            logger.warning("_touch_recalled: failed to update %s", path_str, exc_info=True)


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
    """Used by append_recalled_memories to surface relevant artifacts before each model request.

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
                    return tool_output(
                        f"Skipped (near-identical to {best_artifact.path.name})",
                        ctx=ctx,
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
                return tool_output(
                    f"✓ Saved knowledge ({dedup_action}): {best_artifact.path.name}",
                    ctx=ctx,
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
    fm_dict = {
        "artifact_kind": artifact_kind,
        "title": artifact.title,
        "tags": artifact.tags,
        "created": artifact.created,
        "description": artifact.description,
    }
    with _TRACER.start_as_current_span("co.knowledge.save") as span:
        span.set_attribute("knowledge.artifact_kind", artifact_kind)
        _atomic_write(file_path, file_content)
    _reindex_knowledge_file(ctx, file_path, content, file_content, fm_dict, slug)

    return tool_output(
        f"✓ Saved knowledge: {filename}",
        ctx=ctx,
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
    _atomic_write(file_path, md_content)
    logger.info(f"Saved article {article_id} to {file_path}")

    fm_dict = {
        "artifact_kind": ArtifactKindEnum.ARTICLE.value,
        "title": title,
        "tags": list(tags or []),
        "created": artifact.created,
    }
    try:
        _reindex_knowledge_file(ctx, file_path, content, md_content, fm_dict, slug)
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
    _atomic_write(path, md_content)
    logger.info(f"Consolidated article {artifact_id} (same origin_url)")

    fm_dict = {
        "artifact_kind": ArtifactKindEnum.ARTICLE.value,
        "title": new_title,
        "tags": merged_tags,
        "created": created,
    }
    try:
        _reindex_knowledge_file(ctx, path, new_content, md_content, fm_dict, path.stem)
    except Exception as e:
        logger.warning(f"Failed to reindex consolidated article: {e}")

    return tool_output(
        f"✓ Updated article {artifact_id}: {path.name}\nSource: {origin_url}\nLocation: {path}",
        ctx=ctx,
        article_id=artifact_id,
        action="consolidated",
    )


# Matches line-number prefixes injected by the Read tool (e.g. "1→ " or "Line 1: ")
_LINE_PREFIX_RE = re.compile(r"(^|\n)\d+\u2192 ", re.MULTILINE)
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")


async def append_knowledge(
    ctx: RunContext[CoDeps],
    slug: str,
    content: str,
) -> ToolReturn:
    """Append content to the end of an existing knowledge artifact.

    Use when new information extends an artifact rather than replacing it.
    Safer than update_knowledge when you don't have an exact passage to match.

    *slug* is the full file stem, e.g. "001-dont-use-trailing-comments".
    Use list_knowledge to find it.

    Returns a dict with:
    - display: confirmation message
    - slug: the artifact slug that was appended to

    Args:
        slug: Full file stem of the target artifact (e.g. "003-user-prefers-pytest").
        content: Text to append (added on a new line at the end of the body).
    """
    knowledge_dir = ctx.deps.knowledge_dir
    match = _find_by_slug(knowledge_dir, slug)
    if match is None:
        raise FileNotFoundError(f"Knowledge artifact '{slug}' not found")

    try:
        async with ctx.deps.resource_locks.try_acquire(slug):
            raw = match.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)

            with _TRACER.start_as_current_span("co.knowledge.append") as span:
                span.set_attribute("knowledge.slug", slug)
                span.set_attribute("knowledge.action", "append")

                updated_body = body.rstrip() + "\n" + content
                fm["updated"] = datetime.now(UTC).isoformat()
                md_content = render_frontmatter(fm, updated_body)
                _atomic_write(match, md_content)

                if ctx.deps.knowledge_store is not None:
                    _reindex_knowledge_file(ctx, match, updated_body, md_content, fm, slug)

            return tool_output(
                f"Appended to artifact '{slug}'.",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Artifact '{slug}' is being modified by another tool call — retry next turn",
            ctx=ctx,
        )


async def update_knowledge(
    ctx: RunContext[CoDeps],
    slug: str,
    old_content: str,
    new_content: str,
) -> ToolReturn:
    """Surgically replace a specific passage in a saved knowledge artifact without
    rewriting the entire body. Safer than save_knowledge for targeted edits —
    no dedup path, no full-body replacement.

    *slug* is the full file stem, e.g. "001-dont-use-trailing-comments".
    Use list_knowledge to find it.

    Guards applied before any I/O:
    - Rejects old_content / new_content that contain Read-tool line-number
      prefixes (``1→ `` or ``Line N: ``).
    - old_content must appear exactly once in the body (case-sensitive).

    Returns a dict with:
    - display: confirmation + updated body text
    - slug: the artifact slug that was edited

    Args:
        slug: Full file stem of the target artifact (e.g. "003-user-prefers-pytest").
        old_content: Exact passage to replace (must appear exactly once).
        new_content: Replacement text.
    """
    knowledge_dir = ctx.deps.knowledge_dir
    match = _find_by_slug(knowledge_dir, slug)
    if match is None:
        raise FileNotFoundError(f"Knowledge artifact '{slug}' not found")

    for s, name in ((old_content, "old_content"), (new_content, "new_content")):
        if _LINE_PREFIX_RE.search(s) or _LINE_NUM_RE.search(s):
            raise ValueError(
                f"{name} contains line-number prefixes (e.g. '1\u2192 ' or 'Line N: '). "
                "Strip them before calling update_knowledge."
            )

    try:
        async with ctx.deps.resource_locks.try_acquire(slug):
            raw = match.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)

            body_text = body.expandtabs()
            old_norm = old_content.expandtabs()
            new_norm = new_content.expandtabs()

            count = body_text.count(old_norm)
            if count == 0:
                raise ValueError(
                    f"old_content not found in artifact '{slug}'. "
                    "Check for exact match (case-sensitive, whitespace-sensitive)."
                )
            if count > 1:
                positions: list[int] = []
                pos = 0
                while True:
                    idx = body_text.find(old_norm, pos)
                    if idx == -1:
                        break
                    line_num = body_text[:idx].count("\n") + 1
                    positions.append(line_num)
                    pos = idx + 1
                raise ValueError(
                    f"old_content appears {count} times in '{slug}' "
                    f"(body lines ~{positions}). Provide more context to make it unique."
                )

            with _TRACER.start_as_current_span("co.knowledge.update") as span:
                span.set_attribute("knowledge.slug", slug)
                span.set_attribute("knowledge.action", "update")

                updated_body = body_text.replace(old_norm, new_norm, 1)
                fm["updated"] = datetime.now(UTC).isoformat()
                md_content = render_frontmatter(fm, updated_body)
                _atomic_write(match, md_content)

                if ctx.deps.knowledge_store is not None:
                    _reindex_knowledge_file(ctx, match, updated_body, md_content, fm, slug)

            return tool_output(
                f"Updated artifact '{slug}'.\n{updated_body.strip()}",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Artifact '{slug}' is being modified by another tool call — retry next turn",
            ctx=ctx,
        )
