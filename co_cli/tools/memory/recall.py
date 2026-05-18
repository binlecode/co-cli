"""Memory recall tool — BM25/hybrid search over memory artifacts."""

import logging
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.artifact import MemoryArtifact, load_artifacts
from co_cli.observability.tracing import current_span
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output

logger = logging.getLogger(__name__)

_SNIPPET_DISPLAY_CHARS = 100
"""Maximum chars shown from an artifact snippet in formatted output."""


def _grep_recall(
    artifacts: list[MemoryArtifact],
    query: str,
    max_results: int,
) -> list[MemoryArtifact]:
    """Case-insensitive substring search across title and content."""
    query_lower = query.lower()
    matches = [
        m
        for m in artifacts
        if query_lower in m.content.lower() or query_lower in (m.title or "").lower()
    ]
    matches.sort(key=lambda m: m.updated or m.created, reverse=True)
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


def _list_artifacts(
    ctx: RunContext[CoDeps],
    kinds: list[str] | None,
    limit: int,
    span: Any,
) -> list[dict]:
    """Paginated inventory of memory artifacts, sorted by created descending."""
    if ctx.deps.memory_store is not None:
        rows = ctx.deps.memory_store.list_artifacts(kinds, limit)
        span.set_attribute("memory.artifacts.count", len(rows))
        return rows
    artifacts = load_artifacts(ctx.deps.memory_dir, artifact_kinds=kinds)
    artifacts.sort(key=lambda a: a.created, reverse=True)
    page = artifacts[:limit]
    span.set_attribute("memory.artifacts.count", len(page))
    return [
        _result_dict(
            kind=a.artifact_kind,
            title=a.title,
            snippet=a.content[:_SNIPPET_DISPLAY_CHARS],
            score=0.0,
            path=a.path,
        )
        for a in page
    ]


def _search_artifacts(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
) -> list[dict]:
    """Two-pass FTS recall via MemoryStore; falls back to grep when store is None."""
    store = ctx.deps.memory_store
    if store is None:
        return _grep_artifacts_fallback(ctx, query, kinds, limit)

    hits = store.search_artifacts(query, kinds, limit)
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


def _grep_artifacts_fallback(
    ctx: RunContext[CoDeps],
    query: str,
    kinds: list[str] | None,
    limit: int,
) -> list[dict]:
    """Grep-based artifact search used when MemoryStore is unavailable."""
    grep_kinds = list(kinds or ["user", "rule", "article", "note"])
    if not grep_kinds:
        return []
    artifacts = load_artifacts(ctx.deps.memory_dir, artifact_kinds=grep_kinds)
    matches = _grep_recall(artifacts, query, limit)
    return [
        _result_dict(
            kind=m.artifact_kind,
            title=m.title,
            snippet=m.content[:_SNIPPET_DISPLAY_CHARS],
            score=0.0,
            path=m.path,
        )
        for m in matches
    ]


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
    kinds: list[str] | None = None,
    limit: int = 10,
) -> ToolReturn:
    """Search memory artifacts by keyword, or browse recent artifacts.

    USE THIS for recall of saved preferences, conventions, articles, notes — anything
    the agent has learned or saved to the memory store.

    Empty query → recent N artifacts (title, kind, path, snippet) — browse mode.
    Non-empty → BM25 FTS5/grep search. Load a full artifact body with memory_view(name).

    INTENT → KINDS:
      "what do I prefer / how do I like..."     → kinds=["user"]
      "how do I usually handle / my approach..."→ kinds=["user", "rule"]
      "what do I know about / saved article..." → kinds=["article"]
      "everything about X"                      → kinds=["user", "rule", "article"]
      broad or uncertain intent                 → omit kinds (searches all)

    Search syntax (FTS5): keywords joined with OR (auth OR login), phrases ("connection pool"),
    boolean (python NOT java), prefix (deploy*).

    Result fields: kind, title, snippet, score, path, filename_stem

    Args:
        query: FTS5 keyword query.
        kinds: Up to 3 artifact kinds to filter results. None searches all kinds.
        limit: Max results (default 10).
    """
    span = current_span()
    limit = max(1, int(limit))
    query = query.strip() if query else ""

    if not query:
        artifact_results = _list_artifacts(ctx, kinds, limit, span)
        if not artifact_results:
            return tool_output("No artifacts found.", ctx=ctx, count=0, results=[])
        lines: list[str] = ["\n**Memory artifacts:**"]
        for r in artifact_results:
            kind_str = f" [{r['kind']}]" if r.get("kind") else ""
            path_str = f" @ {r['path']}" if r.get("path") else ""
            lines.append(
                f"  **{r['title']}**{kind_str}{path_str}: "
                f"{(r.get('snippet') or '')[:_SNIPPET_DISPLAY_CHARS]}"
            )
        return tool_output(
            "\n".join(lines), ctx=ctx, count=len(artifact_results), results=artifact_results
        )

    memory_results = _search_artifacts(ctx, query, kinds, limit)
    if not memory_results:
        return tool_output(f"No results found for '{query}'.", ctx=ctx, count=0, results=[])
    return tool_output(
        _format_memory_results(query, memory_results),
        ctx=ctx,
        count=len(memory_results),
        results=memory_results,
    )
