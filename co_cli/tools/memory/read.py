"""Memory inventory tool — `memory_list` over knowledge artifacts.

Full-body reads route through the generic `file_read` tool using the path that
`memory_search` surfaces in its rendered output.
"""

import logging
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.artifact import KnowledgeArtifact, load_knowledge_artifacts
from co_cli.tools.agent_tool import agent_tool
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


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def memory_list(
    ctx: RunContext[CoDeps],
    offset: int = 0,
    limit: int = 20,
    kind: str | None = None,
) -> ToolReturn:
    """List saved memory artifacts with IDs, dates, tags, and one-line summaries.
    Returns one page at a time (default 20 per page).

    Covers all artifact kinds: preferences, decisions, rules, feedback, articles,
    references, and notes. For targeted lookup by keyword, use memory_search.
    For personal notes, use obsidian_list. For cloud documents, use google_drive_search.

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
            f"\nShowing {offset + 1}–{offset + len(page)} of {total}. "
            f"More available — call with offset={offset + limit}."
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
