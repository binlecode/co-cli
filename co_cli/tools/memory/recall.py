"""Memory recall tool — BM25/hybrid search over memory artifacts."""

import logging
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.item import MemoryItem, MemoryKind, load_memory_items
from co_cli.observability.tracing import current_span
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)

_SNIPPET_DISPLAY_CHARS = 100
"""Maximum chars shown from an artifact snippet in formatted output."""


def _grep_recall(
    items: list[MemoryItem],
    query: str,
    max_results: int,
) -> list[MemoryItem]:
    """Case-insensitive substring search across title and content."""
    query_lower = query.lower()
    matches = [
        m
        for m in items
        if query_lower in m.content.lower() or query_lower in (m.title or "").lower()
    ]
    matches.sort(key=lambda m: m.updated_at or m.created_at, reverse=True)
    return matches[:max_results]


def _result_dict(
    *,
    kind: str | None,
    title: str | None,
    snippet: str | None,
    score: float,
    path: str | Path | None,
) -> dict:
    """Shared shape for memory search/list results."""
    path_str = str(path) if path else ""
    stem = Path(path_str).stem if path_str else ""
    return {
        "kind": kind,
        "title": title or stem,
        "snippet": snippet,
        "score": score,
        "path": path_str,
        "filename_stem": stem,
    }


def _list_memory_items(
    ctx: RunContext[CoDeps],
    kinds: list[str] | None,
    limit: int,
    span: Any,
) -> list[dict]:
    """Paginated inventory of memory items, sorted by created descending."""
    if ctx.deps.memory_store is not None:
        rows = ctx.deps.memory_store.list_memory_items(kinds, limit)
        span.set_attribute("memory.items.count", len(rows))
        return rows
    items = load_memory_items(ctx.deps.memory_dir, memory_kinds=kinds)
    items.sort(key=lambda a: a.created_at, reverse=True)
    page = items[:limit]
    span.set_attribute("memory.items.count", len(page))
    return [
        _result_dict(
            kind=a.memory_kind,
            title=a.title,
            snippet=a.content[:_SNIPPET_DISPLAY_CHARS],
            score=0.0,
            path=a.path,
        )
        for a in page
    ]


def _search_memory_items(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
) -> list[dict]:
    """Two-pass FTS recall via MemoryStore; falls back to grep when store is None."""
    store = ctx.deps.memory_store
    if store is None:
        return _grep_memory_items_fallback(ctx, query, kinds, limit)

    hits = store.search_memory_items(query, kinds, limit)
    return [
        _result_dict(
            kind=r.kind,
            title=r.title,
            snippet=r.snippet,
            score=r.score,
            path=r.path,
        )
        for r in hits
    ]


def _grep_memory_items_fallback(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
) -> list[dict]:
    """Grep-based memory item search used when MemoryStore is unavailable."""
    grep_kinds = list(kinds or ["user", "rule", "article", "note"])
    if not grep_kinds:
        return []
    items = load_memory_items(ctx.deps.memory_dir, memory_kinds=grep_kinds)
    matches = _grep_recall(items, query, limit)
    return [
        _result_dict(
            kind=m.memory_kind,
            title=m.title,
            snippet=m.content[:_SNIPPET_DISPLAY_CHARS],
            score=0.0,
            path=m.path,
        )
        for m in matches
    ]


def _record_memory_recall(deps: CoDeps, item_paths: list[Path]) -> None:
    """Update recall metrics (count, last_recalled, recall_days) for each memory item path."""
    from datetime import UTC, datetime

    from co_cli.fileio.atomic import atomic_write_text
    from co_cli.memory.frontmatter import render_memory_item_file
    from co_cli.memory.item import load_memory_item

    now = datetime.now(UTC)
    today_iso = now.date().isoformat()
    for path in item_paths:
        try:
            item = load_memory_item(path)
        except Exception:
            continue
        item.recall_count += 1
        item.last_recalled_at = now.isoformat().replace("+00:00", "Z")
        if today_iso not in item.recall_days:
            item.recall_days.append(today_iso)
        atomic_write_text(path, render_memory_item_file(item))


def _format_memory_results(query: str, results: list[dict]) -> str:
    lines: list[str] = [f"Found {len(results)} memory result(s) for '{query}':\n"]
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
async def memory_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    kinds: list[MemoryKind] | None = None,
    limit: int = 10,
) -> ToolReturn:
    """Search memory artifacts by keyword, or browse recent artifacts.

    Use for recall of saved preferences, conventions, articles, notes — anything the
    agent has learned or saved. Load a full body with memory_view(name).

    Args:
        query: FTS5 keyword query. Default "" lists the most recent `limit` artifacts (browse mode); non-empty runs BM25 search. Syntax: OR, NOT, "phrase", prefix*.
        kinds: Filter to these artifact kinds — any of user, rule, article, note. Default None = all kinds.
        limit: Max results (default 10).
    """
    span = current_span()
    limit = max(1, int(limit))
    query = query.strip() if query else ""

    if not query:
        item_results = _list_memory_items(ctx, kinds, limit, span)
        if not item_results:
            return tool_output("No memory items found.", ctx=ctx, count=0, results=[])
        lines: list[str] = ["\n**Memory items:**"]
        for r in item_results:
            kind_str = f" [{r['kind']}]" if r.get("kind") else ""
            path_str = f" @ {r['path']}" if r.get("path") else ""
            lines.append(
                f"  **{r['title']}**{kind_str}{path_str}: "
                f"{(r.get('snippet') or '')[:_SNIPPET_DISPLAY_CHARS]}"
            )
        return tool_output(
            "\n".join(lines), ctx=ctx, count=len(item_results), results=item_results
        )

    memory_results = _search_memory_items(ctx, query, kinds, limit)
    if not memory_results:
        return tool_output(f"No results found for '{query}'.", ctx=ctx, count=0, results=[])
    item_paths = [Path(r["path"]) for r in memory_results if r.get("path")]
    _record_memory_recall(ctx.deps, item_paths)
    return tool_output(
        _format_memory_results(query, memory_results),
        ctx=ctx,
        count=len(memory_results),
        results=memory_results,
    )
