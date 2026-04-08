"""Memory management tools for persistent knowledge.

This module provides tools for saving, recalling, and listing memories in the
internal knowledge system. Memories are stored as markdown files with YAML
frontmatter in .co-cli/memory/ (project-local).

Retrieval uses FTS5 search when knowledge_search_backend is 'fts5' or 'hybrid'
and knowledge_store is set in deps. Falls back to grep-based search otherwise.
"""

import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# Matches line-number prefixes injected by the Read tool (e.g. "1→ " or "Line 1: ")
_LINE_PREFIX_RE = re.compile(r"(^|\n)\d+\u2192 ", re.MULTILINE)
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")

import yaml
from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext

from co_cli.knowledge._frontmatter import (
    ArtifactTypeEnum,
    parse_frontmatter,
    validate_memory_frontmatter,
)
from co_cli.memory.recall import (
    MemoryEntry,
    load_memories,
    load_always_on_memories,
)
from co_cli._model_factory import ResolvedModel
from co_cli.config._llm import ROLE_SUMMARIZATION
from co_cli.deps import CoDeps
from pydantic_ai.messages import ToolReturn
from co_cli.tools.tool_output import tool_output

_TRACER = otel_trace.get_tracer("co.memory")

logger = logging.getLogger(__name__)

# MemoryEntry, load_memories, load_always_on_memories
# are re-imported from co_cli.memory.recall (extracted to break context/ → tools/ cycle)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug (max 50 chars).

    Args:
        text: Text to slugify

    Returns:
        Slugified text (lowercase, hyphens, no special chars)
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:50]


_HEDGING_PATTERNS: frozenset[str] = frozenset({
    "i think", "maybe", "probably", "might", "not sure",
    "possibly", "i believe", "could be",
})

_CERTAIN_PATTERNS: frozenset[str] = frozenset({
    "always", "never", "definitely", "i always", "i never",
    "i use", "i prefer", "i don't", "i do not",
})


def _classify_certainty(content: str) -> str:
    """Classify memory content into certainty bucket based on keyword heuristics.

    Returns "low" if hedging language detected, "high" if certain assertions
    detected, "medium" as default.
    """
    lower = content.lower()
    if any(phrase in lower for phrase in _HEDGING_PATTERNS):
        return "low"
    if any(phrase in lower for phrase in _CERTAIN_PATTERNS):
        return "high"
    return "medium"


def _detect_provenance(tags: list[str] | None, auto_save_tags: list[str]) -> str:
    """Detect if memory was auto-saved (detected) or explicitly requested (user-told).

    Args:
        tags: Tags list from save_memory call
        auto_save_tags: Tags that indicate auto-save (from config)

    Returns:
        "detected" if signal tags present, "user-told" otherwise
    """
    if not tags:
        return "user-told"

    signal_tags = set(auto_save_tags)
    return "detected" if any(t in signal_tags for t in tags) else "user-told"


def _detect_category(tags: list[str] | None) -> str | None:
    """Extract primary category from tags.

    Args:
        tags: Tags list from save_memory call

    Returns:
        First matching category tag, or None if no category found
    """
    if not tags:
        return None

    categories = ["preference", "correction", "decision", "context", "pattern"]
    for category in categories:
        if category in tags:
            return category

    return None


def _parse_created(created_str: str) -> datetime:
    """Parse an ISO8601 created timestamp to a timezone-aware datetime."""
    return datetime.fromisoformat(created_str.replace("Z", "+00:00"))


def _decay_multiplier(ts_iso: str, half_life_days: int) -> float:
    """Compute exponential decay weight for a memory's age.

    Returns a value in [0, 1]:
      - age_days == 0 → 1.0 (no decay)
      - age_days == half_life_days → ~0.5
      - future-dated → 1.0 (clock-skew guard)

    Args:
        ts_iso: ISO8601 creation timestamp string
        half_life_days: Number of days for weight to halve
    """
    try:
        created = _parse_created(ts_iso)
        now = datetime.now(timezone.utc)
        age_days = max(0, (now - created).days)
        return max(0.0, min(1.0, math.exp(-math.log(2) * age_days / half_life_days)))
    except Exception:
        return 1.0


def grep_recall(
    memories: list[MemoryEntry],
    query: str,
    max_results: int,
) -> list[MemoryEntry]:
    """Case-insensitive substring search across memory content and tags.

    Sorts by recency (updated or created, newest first).
    Temporal decay multiplier is not applied here — grep backend already
    sorts by recency. Decay scoring is applied in the FTS5 path only.
    """
    query_lower = query.lower()
    matches = [
        m
        for m in memories
        if query_lower in m.content.lower()
        or any(query_lower in t.lower() for t in m.tags)
    ]
    matches.sort(key=lambda m: m.updated or m.created, reverse=True)
    return matches[:max_results]




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
    recall_memory). Not required — save directly when the user asks you
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
        _fallback = ResolvedModel(model=None, settings=None)
        _consolidation_resolved = (
            ctx.deps.model_registry.get(ROLE_SUMMARIZATION, _fallback)
            if ctx.deps.model_registry else _fallback
        )
        result = await persist_memory(
            ctx.deps, content, tags, related,
            on_failure="add", resolved=_consolidation_resolved, always_on=always_on,
        )
        meta = result.metadata or {}
        span.set_attribute("memory.action", meta.get("action", "unknown"))
        span.set_attribute("memory.memory_id", meta.get("memory_id", ""))
        if meta.get("decay_triggered"):
            span.set_attribute("memory.decay_triggered", True)
            span.set_attribute("memory.decay_count", meta.get("decay_count", 0))
            span.set_attribute("memory.decay_strategy", meta.get("decay_strategy", ""))

    return result


async def recall_memory(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
    tags: list[str] | None = None,
    tag_match_mode: Literal["any", "all"] = "any",
    created_after: str | None = None,
    created_before: str | None = None,
) -> ToolReturn:
    """Search the internal memory system by keyword. Memories hold cross-session
    knowledge: preferences, decisions, corrections, and research findings.
    Call proactively at conversation start to load context relevant to the
    user's topic. Results include one-hop related memories — connected
    knowledge surfaces automatically.

    For personal notes in the Obsidian vault, use search_notes instead.
    For cloud documents, use search_drive_files.

    Matches against memory content and tags (case-insensitive substring).
    Results are sorted by recency (most recently updated first).

    Also useful before saving new memories, to discover related knowledge
    for linking. Call at the start of a new topic to load relevant context
    from prior conversations.

    Use short keyword queries for best results (e.g. "python testing",
    "database", "dark mode").
    Long phrases may miss matches — the search is substring-based, not semantic.
    If no results are returned, try broader or alternative keywords.

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

    # FTS path — active when backend is 'fts5' or 'hybrid' and index is available
    if ctx.deps.config.knowledge.search_backend in ("fts5", "hybrid") and ctx.deps.knowledge_store is not None:
        try:
            fts_results = ctx.deps.knowledge_store.search(
                query,
                source="memory",
                kind="memory",
                tags=tags,
                tag_match_mode=tag_match_mode,
                created_after=created_after,
                created_before=created_before,
                limit=max_results * 4,
            )
            if not fts_results:
                return tool_output(
                    f"No memories found matching '{query}'",
                    ctx=ctx,
                    count=0,
                    results=[],
                )
            # Load only the FTS-pointed files — O(k) not O(N)
            path_to_bm25: dict[str, float] = {r.path: r.score for r in fts_results}
            raw_matches: list[MemoryEntry] = []
            for r in fts_results:
                p = Path(r.path)
                if not p.exists():
                    continue
                try:
                    raw = p.read_text(encoding="utf-8")
                    fm, body = parse_frontmatter(raw)
                    validate_memory_frontmatter(fm)
                    raw_matches.append(MemoryEntry(
                        id=fm["id"],
                        path=p,
                        content=body.strip(),
                        tags=fm.get("tags", []),
                        created=fm["created"],
                        updated=fm.get("updated"),
                        decay_protected=fm.get("decay_protected", False),
                        related=fm.get("related"),
                        kind=fm.get("kind", "memory"),
                        artifact_type=fm.get("artifact_type"),
                        always_on=fm.get("always_on", False),
                    ))
                except Exception as e:
                    logger.warning(f"Failed to load FTS match {r.path}: {e}")
            # Exclude session-summary artifacts from default recall
            raw_matches = [m for m in raw_matches if m.artifact_type != ArtifactTypeEnum.SESSION_SUMMARY]
            # Composite relevance + decay scoring — preserves lexical signal alongside recency.
            # r.score uses 1/(1+abs(rank)) convention (lower = stronger match); reinvert
            # so higher relevance = better match before combining with decay.
            half_life = ctx.deps.config.memory.recall_half_life_days
            scored: list[tuple[float, MemoryEntry]] = []
            for entry in raw_matches:
                relevance = 1.0 - path_to_bm25.get(str(entry.path), 0.5)
                if entry.decay_protected:
                    decay = 1.0
                else:
                    decay = _decay_multiplier(entry.created, half_life)
                composite = 0.6 * relevance + 0.4 * decay
                scored.append((composite, entry))
            scored.sort(key=lambda x: x[0], reverse=True)
            matches = [e for _, e in scored][:max_results]
        except Exception as e:
            logger.warning(f"FTS recall failed, falling back to grep: {e}")
            # Fall through to grep path
            memories = load_memories(memory_dir)
            if tags:
                if tag_match_mode == "all":
                    memories = [m for m in memories if all(t in m.tags for t in tags)]
                else:
                    memories = [m for m in memories if any(t in m.tags for t in tags)]
            if created_after:
                memories = [m for m in memories if m.created and m.created >= created_after]
            if created_before:
                memories = [m for m in memories if m.created and m.created <= created_before]
            memories = [m for m in memories if m.artifact_type != ArtifactTypeEnum.SESSION_SUMMARY]
            matches = grep_recall(memories, query, max_results)
    else:
        memories = load_memories(memory_dir)
        if tags:
            if tag_match_mode == "all":
                memories = [m for m in memories if all(t in m.tags for t in tags)]
            else:
                memories = [m for m in memories if any(t in m.tags for t in tags)]
        if created_after:
            memories = [m for m in memories if m.created and m.created >= created_after]
        if created_before:
            memories = [m for m in memories if m.created and m.created <= created_before]
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
        f"Found {len(matches)} memor{'y' if len(matches) == 1 else 'ies'} "
        f"matching '{query}':\n"
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
    """Dedicated semantic search over saved memories. Use this to look up
    preferences, decisions, corrections, and context facts saved across sessions.

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

    # FTS path — active when backend is 'fts5' or 'hybrid' and index is available
    if ctx.deps.config.knowledge.search_backend in ("fts5", "hybrid") and ctx.deps.knowledge_store is not None:
        try:
            results = ctx.deps.knowledge_store.search(
                query,
                source="memory",
                kind="memory",
                tags=tags,
                tag_match_mode=tag_match_mode,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
            )
            otel_trace.get_current_span().set_attribute("rag.backend", ctx.deps.config.knowledge.search_backend)
            if not results:
                return tool_output(f"No memories found matching '{query}'", ctx=ctx, count=0, results=[])

            # Exclude session-summary artifacts by reading each hit's frontmatter
            filtered = []
            for r in results:
                if r.path:
                    try:
                        fm, _ = parse_frontmatter(Path(r.path).read_text(encoding="utf-8"))
                        if fm.get("artifact_type") == ArtifactTypeEnum.SESSION_SUMMARY:
                            continue
                    except Exception:
                        pass
                filtered.append(r)
            results = filtered
            if not results:
                return tool_output(f"No memories found matching '{query}'", ctx=ctx, count=0, results=[])

            lines = [f"Found {len(results)} memor{'y' if len(results) == 1 else 'ies'} matching '{query}':\n"]
            result_dicts = []
            for r in results:
                title_str = r.title or Path(r.path).stem if r.path else "unknown"
                lines.append(f"**{title_str}** (score: {r.score:.3f})")
                if r.tags:
                    lines.append(f"Tags: {r.tags}")
                if r.snippet:
                    lines.append(f"{r.snippet}\n")
                result_dicts.append({
                    "source": r.source,
                    "kind": r.kind,
                    "title": r.title,
                    "snippet": r.snippet,
                    "score": r.score,
                    "path": r.path,
                })
            return tool_output(
                "\n".join(lines).rstrip(),
                ctx=ctx,
                count=len(results),
                results=result_dicts,
            )
        except Exception as e:
            logger.warning(f"search_memories FTS error, falling back to grep: {e}")

    otel_trace.get_current_span().set_attribute("rag.backend", "grep")
    # Grep fallback
    memories = load_memories(memory_dir, kind="memory")
    if tags:
        if tag_match_mode == "all":
            memories = [m for m in memories if all(t in m.tags for t in tags)]
        else:
            memories = [m for m in memories if any(t in m.tags for t in tags)]
    if created_after:
        memories = [m for m in memories if m.created and m.created >= created_after]
    if created_before:
        memories = [m for m in memories if m.created and m.created <= created_before]
    memories = [m for m in memories if m.artifact_type != ArtifactTypeEnum.SESSION_SUMMARY]

    matches = grep_recall(memories, query, limit)
    if not matches:
        return tool_output(f"No memories found matching '{query}'", ctx=ctx, count=0, results=[])

    lines = [f"Found {len(matches)} memor{'y' if len(matches) == 1 else 'ies'} matching '{query}':\n"]
    result_dicts = []
    for m in matches:
        lines.append(f"**{m.path.stem}** [{m.kind}]: {m.content[:100]}")
        result_dicts.append({
            "source": "memory",
            "kind": m.kind,
            "title": m.path.stem,
            "snippet": m.content[:100],
            "score": 0.0,
            "path": str(m.path),
        })
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
    and research findings. For targeted lookup by keyword, use recall_memory.
    For personal notes, use list_notes. For cloud documents, use
    search_drive_files.

    Use this for a full inventory or capacity check. Keep paginating until
    has_more is false when you need a complete listing.

    Returns a dict with:
    - display: formatted memory inventory — show directly to the user
    - count: number of memories in this page
    - total: total number of memories across all pages
    - offset: starting position of this page
    - limit: page size requested
    - has_more: true if more pages exist beyond this one
    - capacity: configured memory capacity limit
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
            capacity=ctx.deps.config.memory.max_count,
            memories=[],
        )

    # Sort by ID
    memories.sort(key=lambda m: str(m.id))
    total = len(memories)

    # Paginate
    page = memories[offset:offset + limit]

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
                "auto_category": _detect_category(m.tags),
                "summary": summary,
                "decay_protected": m.decay_protected,
            }
        )

    has_more = offset + limit < total

    # Format as markdown list with lifecycle indicators
    lines = [f"Total memories: {total}/{ctx.deps.config.memory.max_count}\n"]

    for md in memory_dicts:
        # Format dates
        created_date = md["created"][:10]
        if md.get("updated"):
            updated_date = md["updated"][:10]
            date_str = f"{created_date} → {updated_date}"
        else:
            date_str = created_date

        # Format category
        category_str = (
            f" [{md['auto_category']}]" if md.get("auto_category") else ""
        )

        # Format protection indicator
        protected_str = " 🔒" if md.get("decay_protected") else ""

        kind_str = f" [{md.get('kind', 'memory')}]"
        artifact_str = f" ({md['artifact_type']})" if md.get("artifact_type") else ""
        display_id = str(md['id'])[:8] if isinstance(md['id'], str) else f"{md['id']:03d}"
        lines.append(
            f"**{display_id}** ({date_str}){kind_str}{artifact_str}{category_str}{protected_str} "
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
        capacity=ctx.deps.config.memory.max_count,
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
                fm["updated"] = datetime.now(timezone.utc).isoformat()
                md_content = (
                    f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n"
                    f"{updated_body.strip()}\n"
                )
                match.write_text(md_content, encoding="utf-8")

                if ctx.deps.knowledge_store is not None:
                    try:
                        import hashlib as _hashlib
                        ctx.deps.knowledge_store.index(
                            source="memory",
                            kind=fm.get("kind", "memory"),
                            path=str(match),
                            title=slug,
                            content=updated_body.strip(),
                            mtime=match.stat().st_mtime,
                            hash=_hashlib.sha256(md_content.encode()).hexdigest(),
                            tags=" ".join(fm.get("tags", [])),
                            created=fm.get("created"),
                            updated=fm.get("updated"),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to reindex updated memory '{slug}': {e}")

            return tool_output(
                f"Updated memory '{slug}'.\n{updated_body.strip()}",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(f"Memory '{slug}' is being modified by another tool call — retry next turn")


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
                fm["updated"] = datetime.now(timezone.utc).isoformat()
                md_content = (
                    f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n"
                    f"{updated_body.strip()}\n"
                )
                match.write_text(md_content, encoding="utf-8")

                if ctx.deps.knowledge_store is not None:
                    try:
                        import hashlib as _hashlib
                        ctx.deps.knowledge_store.index(
                            source="memory",
                            kind=fm.get("kind", "memory"),
                            path=str(match),
                            title=slug,
                            content=updated_body.strip(),
                            mtime=match.stat().st_mtime,
                            hash=_hashlib.sha256(md_content.encode()).hexdigest(),
                            tags=" ".join(fm.get("tags", [])),
                            created=fm.get("created"),
                            updated=fm.get("updated"),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to reindex appended memory '{slug}': {e}")

            return tool_output(
                f"Appended to '{slug}'.",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(f"Memory '{slug}' is being modified by another tool call — retry next turn")
