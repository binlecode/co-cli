"""Memory management tools for persistent knowledge.

This module provides tools for saving, recalling, and listing memories in the
internal knowledge system. Memories are stored as markdown files with YAML
frontmatter in .co-cli/memory/ (project-local).

Retrieval uses grep-based search across all memory files. Results are sorted by
recency. FTS5/BM25 is not used for memories — only for articles and external sources.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal

# Matches line-number prefixes injected by the Read tool (e.g. "1→ " or "Line 1: ")
_LINE_PREFIX_RE = re.compile(r"(^|\n)\d+\u2192 ", re.MULTILINE)
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")

import yaml
from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.deps import CoDeps
from co_cli.knowledge._frontmatter import (
    ArtifactTypeEnum,
    parse_frontmatter,
)
from co_cli.memory.recall import (
    MemoryEntry,
    load_memories,
)
from co_cli.tools.tool_output import tool_output

_TRACER = otel_trace.get_tracer("co.memory")

logger = logging.getLogger(__name__)

# MemoryEntry and load_memories are re-imported from co_cli.memory.recall
# (extracted to break context/ → tools/ cycle)


def grep_recall(
    memories: list[MemoryEntry],
    query: str,
    max_results: int,
) -> list[MemoryEntry]:
    """Case-insensitive substring search across memory content and tags.

    Sorts by recency (updated or created, newest first).
    """
    query_lower = query.lower()
    matches = [
        m
        for m in memories
        if query_lower in m.content.lower() or any(query_lower in t.lower() for t in m.tags)
    ]
    matches.sort(key=lambda m: m.updated or m.created, reverse=True)
    return matches[:max_results]


def filter_memories(
    entries: list[MemoryEntry],
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> list[MemoryEntry]:
    """Filter a list of MemoryEntry by tags and date range.

    Args:
        entries: Source list to filter (not mutated).
        tags: Exact tag filter list. None = no filter.
        tag_match_mode: 'any' (OR) or 'all' (AND — entry must have every tag).
        created_after: ISO8601 date string; keep entries created on or after this date.
        created_before: ISO8601 date string; keep entries created on or before this date.

    Returns:
        Filtered list (new list, same MemoryEntry objects).
    """
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


async def save_memory(
    ctx: RunContext[CoDeps],
    content: str,
    tags: list[str] | None = None,
    related: list[str] | None = None,
    always_on: bool = False,
) -> ToolReturn:
    """Saves a memory. If a near-duplicate exists, the existing memory is
    updated instead of creating a new file. Always use save_memory to persist
    facts — never call update_memory for dedup purposes. update_memory is for
    surgical find-and-replace edits only.

    When to save — detect these signals proactively:
    - Preference: "I always use 4-space indentation", "I prefer dark themes"
    - Correction: "Actually we switched from Flask to FastAPI last month"
    - Decision: "We've decided to use Kubernetes for production"
    - Pattern: "We always review PRs before merging"
    - Research finding: persist results after investigating something

    Save when you detect the signal — do not wait for "remember this."
    Duplicates and near-matches are auto-consolidated, so saving liberally
    is safe.

    Do NOT save: workspace paths, transient errors, session-only context,
    or sensitive information (credentials, health, financial).

    Optionally include related memory slugs for knowledge linking (see
    search_memories). Not required — save directly when the user asks you
    to remember something.

    Write content in third person: "User prefers pytest over unittest",
    not "I prefer pytest". Keeps memories unambiguous when recalled later.

    Returns a dict with:
    - display: confirmation message — show directly to the user
    - memory_id: assigned ID
    - action: "saved" (new) or "consolidated" (merged with existing duplicate)

    Args:
        content: Memory text in third person (markdown, < 500 chars recommended).
        tags: Categorization tags. Use signal type as first tag:
              ["preference", ...], ["correction", ...], ["decision", ...].
        related: Slugs of related memories for knowledge linking
                 (e.g. ["003-user-prefers-pytest"]).
    """
    from co_cli.memory._lifecycle import persist_memory

    with _TRACER.start_as_current_span("co.memory.save") as span:
        span.set_attribute("memory.tags", ",".join(tags or []))
        _model = ctx.deps.model.model if ctx.deps.model else None
        result = await persist_memory(
            ctx.deps,
            content,
            tags,
            related,
            on_failure="add",
            model=_model,
            model_settings=NOREASON_SETTINGS,
            always_on=always_on,
        )
        meta = result.metadata or {}
        span.set_attribute("memory.action", meta.get("action", "unknown"))
        span.set_attribute("memory.memory_id", meta.get("memory_id", ""))
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
    """Used by inject_opening_context to surface relevant memories before each model request.

    Matches against memory content and tags (case-insensitive substring).
    Results are sorted by recency (most recently updated first).
    Results include one-hop related memories — connected knowledge surfaces automatically.

    Returns a dict with:
    - display: formatted memory list — show directly to the user
    - count: number of memories found (including related hops)
    - results: list of {id, content, tags, created} dicts

    Args:
        query: Keywords to search (e.g. "python testing", "database", "preference").
        max_results: Max direct matches to return (default 5). Related memories
                     are appended beyond this limit.
        tags: Exact tag filter list. None = no filter.
        tag_match_mode: 'any' (OR — at least one tag matches) or 'all' (AND — all tags match).
        created_after: ISO8601 date string; only return memories created on or after this date.
        created_before: ISO8601 date string; only return memories created on or before this date.
    """
    memory_dir = ctx.deps.memory_dir

    memories = load_memories(memory_dir)
    memories = filter_memories(memories, tags, tag_match_mode, created_after, created_before)
    memories = [m for m in memories if m.artifact_type != ArtifactTypeEnum.SESSION_SUMMARY]
    matches = grep_recall(memories, query, max_results)

    if not matches:
        return tool_output(
            f"No memories found matching '{query}'",
            ctx=ctx,
            count=0,
            results=[],
        )

    # One-hop traversal: surface related memories (§14.1)
    match_ids = {str(m.id) for m in matches}
    # Lazy full load: only when matched entries have related slugs to follow
    has_related = any(m.related for m in matches)
    if has_related:
        _all_memories = load_memories(memory_dir)
        all_by_slug: dict[str, MemoryEntry] = {m.path.stem: m for m in _all_memories}
    else:
        all_by_slug: dict[str, MemoryEntry] = {}

    related_entries: list[MemoryEntry] = []
    for m in matches:
        if not m.related:
            continue
        for slug in m.related:
            linked = all_by_slug.get(slug)
            if linked and str(linked.id) not in match_ids:
                related_entries.append(linked)
                match_ids.add(str(linked.id))
            if len(related_entries) >= 5:
                break
        if len(related_entries) >= 5:
            break

    # Format as markdown list
    lines = [
        f"Found {len(matches)} memor{'y' if len(matches) == 1 else 'ies'} matching '{query}':\n"
    ]
    result_dicts: list[dict[str, Any]] = []
    for r in matches:
        display_id = str(r.id)[:8] if isinstance(r.id, str) else str(r.id)
        lines.append(f"**Memory {display_id}** (created {r.created[:10]})")
        if r.tags:
            lines.append(f"Tags: {', '.join(r.tags)}")
        lines.append(f"{r.content}\n")
        result_dicts.append(
            {
                "id": r.id,
                "path": str(r.path),
                "content": r.content,
                "tags": r.tags,
                "created": r.created,
            }
        )

    # Append related memories section
    if related_entries:
        lines.append("**Related memories:**\n")
        for r in related_entries:
            display_id = str(r.id)[:8] if isinstance(r.id, str) else str(r.id)
            lines.append(f"**Memory {display_id}** (created {r.created[:10]})")
            if r.tags:
                lines.append(f"Tags: {', '.join(r.tags)}")
            lines.append(f"{r.content}\n")
            result_dicts.append(
                {
                    "id": r.id,
                    "path": str(r.path),
                    "content": r.content,
                    "tags": r.tags,
                    "created": r.created,
                    "related_hop": True,
                }
            )

    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(matches) + len(related_entries),
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

    memory_dir = ctx.deps.memory_dir

    otel_trace.get_current_span().set_attribute("rag.backend", "grep")
    memories = load_memories(memory_dir, kind="memory")
    memories = filter_memories(memories, tags, tag_match_mode, created_after, created_before)
    memories = [m for m in memories if m.artifact_type != ArtifactTypeEnum.SESSION_SUMMARY]

    matches = grep_recall(memories, query, limit)
    if not matches:
        return tool_output(f"No memories found matching '{query}'", ctx=ctx, count=0, results=[])

    lines = [
        f"Found {len(matches)} memor{'y' if len(matches) == 1 else 'ies'} matching '{query}':\n"
    ]
    result_dicts = []
    for m in matches:
        lines.append(f"**{m.path.stem}** [{m.kind}]: {m.content[:100]}")
        result_dicts.append(
            {
                "source": "memory",
                "kind": m.kind,
                "title": m.path.stem,
                "snippet": m.content[:100],
                "score": 0.0,
                "path": str(m.path),
            }
        )
    return tool_output("\n".join(lines), ctx=ctx, count=len(matches), results=result_dicts)


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

    Use this for a full inventory. Keep paginating until
    has_more is false when you need a complete listing.

    Returns a dict with:
    - display: formatted memory inventory — show directly to the user
    - count: number of memories in this page
    - total: total number of memories across all pages
    - offset: starting position of this page
    - limit: page size requested
    - has_more: true if more pages exist beyond this one
    - memories: list of summary dicts with id, created, tags, summary, kind

    Args:
        offset: Starting position (0-based). Example: offset=20 skips the
                first 20 memories.
        limit: Max memories per page (default 20).
        kind: Filter by kind — "memory", "article", or None for all.
              Passing kind="article" returns only saved articles.
              Passing kind="memory" returns only conversation memories.
    """
    memory_dir = ctx.deps.memory_dir
    memories = load_memories(memory_dir, kind=kind)

    if not memories:
        no_dir = not memory_dir.exists()
        kind_note = f" (kind={kind})" if kind else ""
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

    # Sort by ID
    memories.sort(key=lambda m: str(m.id))
    total = len(memories)

    # Paginate
    page = memories[offset : offset + limit]

    # Build summary dicts
    memory_dicts: list[dict[str, Any]] = []
    for m in page:
        body_lines = m.content.split("\n")
        summary = body_lines[0] if body_lines else "(empty)"
        if len(summary) > 80:
            summary = summary[:77] + "..."

        memory_dicts.append(
            {
                "id": m.id,
                "kind": m.kind,
                "artifact_type": m.artifact_type,
                "created": m.created,
                "updated": m.updated,
                "tags": m.tags,
                "type": m.type,
                "summary": summary,
            }
        )

    has_more = offset + limit < total

    # Format as markdown list with lifecycle indicators
    lines = [f"Total memories: {total}\n"]

    for md in memory_dicts:
        # Format dates
        created_date = md["created"][:10]
        if md.get("updated"):
            updated_date = md["updated"][:10]
            date_str = f"{created_date} → {updated_date}"
        else:
            date_str = created_date

        # Format type label
        category_str = f" [{md['type']}]" if md.get("type") else ""

        kind_str = f" [{md.get('kind', 'memory')}]"
        artifact_str = f" ({md['artifact_type']})" if md.get("artifact_type") else ""
        display_id = str(md["id"])[:8] if isinstance(md["id"], str) else f"{md['id']:03d}"
        lines.append(
            f"**{display_id}** ({date_str}){kind_str}{artifact_str}{category_str} "
            f": {md['summary']}"
        )

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
    knowledge_dir = ctx.deps.memory_dir
    match = next((p for p in knowledge_dir.glob("*.md") if p.stem == slug), None)
    if match is None:
        raise FileNotFoundError(f"Memory '{slug}' not found")

    # Guard: reject Read-tool line-number artifacts
    for s, name in ((old_content, "old_content"), (new_content, "new_content")):
        if _LINE_PREFIX_RE.search(s) or _LINE_NUM_RE.search(s):
            raise ValueError(
                f"{name} contains line-number prefixes (e.g. '1\u2192 ' or 'Line N: '). "
                "Strip them before calling update_memory."
            )

    from co_cli.tools.resource_lock import ResourceBusyError
    from co_cli.tools.tool_errors import tool_error

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
                md_content = (
                    f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n"
                    f"{updated_body.strip()}\n"
                )
                match.write_text(md_content, encoding="utf-8")

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
    from co_cli.tools.resource_lock import ResourceBusyError
    from co_cli.tools.tool_errors import tool_error

    knowledge_dir = ctx.deps.memory_dir
    match = next((p for p in knowledge_dir.glob("*.md") if p.stem == slug), None)
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
                md_content = (
                    f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n"
                    f"{updated_body.strip()}\n"
                )
                match.write_text(md_content, encoding="utf-8")

            return tool_output(
                f"Appended to '{slug}'.",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Memory '{slug}' is being modified by another tool call — retry next turn"
        )
