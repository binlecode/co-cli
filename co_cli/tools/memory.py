"""Memory/knowledge tools — recall, list, save, and edit persistent artifacts.

Artifacts are stored as markdown files with ``kind: knowledge`` frontmatter in
``ctx.deps.knowledge_dir`` (default ``~/.co-cli/knowledge/``). An
``artifact_kind`` subtype distinguishes preferences, rules, feedback, articles,
references, and notes. FTS5/BM25 via ``knowledge_store`` powers search under
``source="knowledge"``; degrades to empty when the store is unavailable.

``save_knowledge`` is the sole write path, exposed to the extractor sub-agent.
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

# Matches line-number prefixes injected by the Read tool (e.g. "1→ " or "Line 1: ")
_LINE_PREFIX_RE = re.compile(r"(^|\n)\d+\u2192 ", re.MULTILINE)
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.knowledge._artifact import (
    ArtifactKindEnum,
    KnowledgeArtifact,
    PinModeEnum,
    SourceTypeEnum,
    load_knowledge_artifacts,
)
from co_cli.knowledge._frontmatter import (
    parse_frontmatter,
    render_frontmatter,
    render_knowledge_file,
)
from co_cli.tools.resource_lock import ResourceBusyError
from co_cli.tools.tool_io import tool_error, tool_output, tool_output_raw

_TRACER = otel_trace.get_tracer("co.memory")

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug, max 50 chars."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _find_by_slug(knowledge_dir: Path, slug: str) -> Path | None:
    """Return the knowledge file whose stem matches slug, or None."""
    return next((p for p in knowledge_dir.glob("*.md") if p.stem == slug), None)


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


def filter_memories(
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


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


async def _recall_for_context(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> ToolReturn:
    """Used by inject_opening_context to surface relevant memories before each model request.

    Uses FTS5/BM25 DB search via knowledge_store. Returns empty when knowledge_store
    is None (degraded mode — no crash).

    Returns a dict with:
    - display: formatted memory list — show directly to the user
    - count: number of memories found
    - results: list of {path, title, snippet, tags, created, score} dicts

    Args:
        query: Keywords to search (e.g. "python testing", "database", "preference").
        max_results: Max results to return (default 5).
        tags: Exact tag filter list. None = no filter.
        tag_match_mode: 'any' (OR — at least one tag matches) or 'all' (AND — all tags match).
        created_after: ISO8601 date string; only return memories created on or after this date.
        created_before: ISO8601 date string; only return memories created on or before this date.
    """
    if ctx.deps.knowledge_store is None:
        return tool_output("", ctx=ctx, count=0, results=[])

    results = ctx.deps.knowledge_store.search(
        query,
        source="knowledge",
        tags=tags,
        tag_match_mode=tag_match_mode,
        created_after=created_after,
        created_before=created_before,
        limit=max_results,
    )

    if not results:
        return tool_output(
            f"No memories found matching '{query}'",
            ctx=ctx,
            count=0,
            results=[],
        )

    lines = [
        f"Found {len(results)} memor{'y' if len(results) == 1 else 'ies'} matching '{query}':\n"
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


async def search_memories(
    ctx: RunContext[CoDeps],
    query: str,
    *,
    limit: int = 10,
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> ToolReturn:
    """Keyword search over saved memories. Use this to look up preferences,
    decisions, corrections, and context facts saved across sessions.

    For knowledge articles and external sources, use search_knowledge instead.

    Returns a dict with:
    - display: formatted ranked results — show directly to the user
    - count: number of results
    - results: list of {source, kind, title, snippet, score, path} dicts

    Args:
        query: Free-text search query (e.g. "python testing", "database preference").
        limit: Max results to return (default 10).
        tags: Tag filter list. None = no filter.
        tag_match_mode: 'any' (OR) or 'all' (AND — doc must have every tag).
        created_after: ISO8601 date string; only return items created on or after this date.
        created_before: ISO8601 date string; only return items created on or before this date.
    """
    if not query.strip():
        return tool_output("Query is required.", ctx=ctx, count=0, results=[])
    if limit < 1:
        return tool_output("limit must be >= 1.", ctx=ctx, count=0, results=[])

    if ctx.deps.knowledge_store is None:
        return tool_error("Knowledge store unavailable — memory search requires DB index")

    otel_trace.get_current_span().set_attribute("rag.backend", "fts5")
    results = ctx.deps.knowledge_store.search(
        query,
        source="knowledge",
        tags=tags,
        tag_match_mode=tag_match_mode,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
    )

    if not results:
        return tool_output(f"No memories found matching '{query}'", ctx=ctx, count=0, results=[])

    lines = [
        f"Found {len(results)} memor{'y' if len(results) == 1 else 'ies'} matching '{query}':\n"
    ]
    result_dicts = []
    for r in results:
        lines.append(f"**{r.title or r.path}** [{r.kind or 'memory'}]: {r.snippet or ''}")
        result_dicts.append(
            {
                "source": r.source,
                "kind": r.kind,
                "title": r.title,
                "snippet": r.snippet,
                "score": r.score,
                "path": r.path,
            }
        )
    return tool_output("\n".join(lines), ctx=ctx, count=len(results), results=result_dicts)


async def list_memories(
    ctx: RunContext[CoDeps],
    offset: int = 0,
    limit: int = 20,
    kind: str | None = None,
) -> ToolReturn:
    """List saved memories with IDs, dates, tags, and one-line summaries.
    Returns one page at a time (default 20 per page).

    Memories are cross-session knowledge: preferences, decisions, corrections,
    and research findings. For targeted lookup by keyword, use search_memories.
    For personal notes, use list_notes. For cloud documents, use
    search_drive_files.

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
    lines = [f"Total memories: {total}\n"]
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
    pin_mode: str = PinModeEnum.NONE.value,
) -> ToolReturn:
    """Save a reusable knowledge artifact (preference, rule, feedback, decision, article, reference, note).

    Writes a canonical kind=knowledge markdown file under ctx.deps.knowledge_dir
    and indexes it under source='knowledge' so search_knowledge can retrieve it.
    Two calls with identical content produce two distinct files (UUID suffix);
    dedup lives in a later phase.

    Args:
        content: Primary text of the artifact.
        artifact_kind: One of preference | decision | rule | feedback | article | reference | note.
        title: Optional human-readable label.
        description: Optional ≤200-char hook used for manifest dedup later.
        tags: Optional retrieval labels (lowercased before writing).
        pin_mode: 'standing' to inject as always-on context; 'none' by default.
    """
    valid_kinds = {e.value for e in ArtifactKindEnum}
    if artifact_kind not in valid_kinds:
        raise ValueError(
            f"Unknown artifact_kind: {artifact_kind!r}. Valid values: {sorted(valid_kinds)}"
        )

    knowledge_dir = ctx.deps.knowledge_dir
    knowledge_dir.mkdir(parents=True, exist_ok=True)

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
        pin_mode=pin_mode,
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
    handles both at once, but update_memory/append_memory mutate a single file
    and need to refresh the DB inline.
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


async def update_memory(
    ctx: RunContext[CoDeps],
    slug: str,
    old_content: str,
    new_content: str,
) -> ToolReturn:
    """Surgically replace a specific passage in a memory file without rewriting
    the entire body.  Safer than save_memory for targeted edits — no dedup
    path, no full-body replacement.

    *slug* is the full file stem, e.g. ``"001-dont-use-trailing-comments"``.
    Use list_memories to find it.

    Guards applied before any I/O:
    - Rejects old_content / new_content that contain Read-tool line-number
      prefixes (``1→ `` or ``Line N: ``).
    - old_content must appear exactly once in the body (case-sensitive).

    Returns a dict with:
    - display: confirmation + updated body text
    - slug: the memory slug that was edited

    Args:
        slug: Full file stem of the target memory (e.g. "003-user-prefers-pytest").
        old_content: Exact passage to replace (must appear exactly once).
        new_content: Replacement text.
    """
    knowledge_dir = ctx.deps.knowledge_dir
    match = _find_by_slug(knowledge_dir, slug)
    if match is None:
        raise FileNotFoundError(f"Memory '{slug}' not found")

    # Guard: reject Read-tool line-number artifacts
    for s, name in ((old_content, "old_content"), (new_content, "new_content")):
        if _LINE_PREFIX_RE.search(s) or _LINE_NUM_RE.search(s):
            raise ValueError(
                f"{name} contains line-number prefixes (e.g. '1\u2192 ' or 'Line N: '). "
                "Strip them before calling update_memory."
            )

    try:
        async with ctx.deps.resource_locks.try_acquire(slug):
            raw = match.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)

            # Tab normalization — treat tabs and equivalent spaces as equivalent
            body_text = body.expandtabs()
            old_norm = old_content.expandtabs()
            new_norm = new_content.expandtabs()

            count = body_text.count(old_norm)
            if count == 0:
                raise ValueError(
                    f"old_content not found in memory '{slug}'. "
                    "Check for exact match (case-sensitive, whitespace-sensitive)."
                )
            if count > 1:
                # Find line numbers of each occurrence for a useful error message
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

            with _TRACER.start_as_current_span("co.memory.update") as span:
                span.set_attribute("memory.slug", slug)
                span.set_attribute("memory.action", "update")

                updated_body = body_text.replace(old_norm, new_norm, 1)
                fm["updated"] = datetime.now(UTC).isoformat()
                md_content = render_frontmatter(fm, updated_body)
                with tempfile.NamedTemporaryFile(
                    "w", dir=match.parent, suffix=".tmp", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(md_content)
                os.replace(tmp.name, match)

                if ctx.deps.knowledge_store is not None:
                    _reindex_knowledge_file(ctx, match, updated_body, md_content, fm, slug)

            return tool_output(
                f"Updated memory '{slug}'.\n{updated_body.strip()}",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Memory '{slug}' is being modified by another tool call — retry next turn"
        )


async def append_memory(
    ctx: RunContext[CoDeps],
    slug: str,
    content: str,
) -> ToolReturn:
    """Append content to the end of an existing memory file.

    Use when new information extends a memory rather than replacing it.
    Safer than update_memory when you don't have an exact passage to match.

    *slug* is the full file stem, e.g. ``"001-dont-use-trailing-comments"``.
    Use list_memories to find it.

    Returns a dict with:
    - display: confirmation message
    - slug: the memory slug that was appended to

    Args:
        slug: Full file stem of the target memory (e.g. "003-user-prefers-pytest").
        content: Text to append (added on a new line at the end of the body).
    """
    knowledge_dir = ctx.deps.knowledge_dir
    match = _find_by_slug(knowledge_dir, slug)
    if match is None:
        raise FileNotFoundError(f"Memory '{slug}' not found")

    try:
        async with ctx.deps.resource_locks.try_acquire(slug):
            raw = match.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)

            with _TRACER.start_as_current_span("co.memory.append") as span:
                span.set_attribute("memory.slug", slug)
                span.set_attribute("memory.action", "append")

                updated_body = body.rstrip() + "\n" + content
                fm["updated"] = datetime.now(UTC).isoformat()
                md_content = render_frontmatter(fm, updated_body)
                with tempfile.NamedTemporaryFile(
                    "w", dir=match.parent, suffix=".tmp", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(md_content)
                os.replace(tmp.name, match)

                if ctx.deps.knowledge_store is not None:
                    _reindex_knowledge_file(ctx, match, updated_body, md_content, fm, slug)

            return tool_output(
                f"Appended to '{slug}'.",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Memory '{slug}' is being modified by another tool call — retry next turn"
        )
