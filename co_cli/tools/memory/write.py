"""Memory write tools — `memory_create` and `memory_modify` over knowledge artifacts."""

import logging
from typing import Literal

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.artifact import ArtifactKindEnum
from co_cli.memory.service import mutate_artifact, reindex, save_artifact
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.resource_lock import ResourceBusyError
from co_cli.tools.tool_io import tool_error, tool_output

_TRACER = otel_trace.get_tracer("co.knowledge")

logger = logging.getLogger(__name__)


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    approval=True,
    is_concurrent_safe=True,
    retries=1,
)
async def memory_create(
    ctx: RunContext[CoDeps],
    content: str,
    artifact_kind: str,
    title: str | None = None,
    description: str | None = None,
    source_url: str | None = None,
    decay_protected: bool = False,
) -> ToolReturn:
    """Save a new knowledge artifact or update an existing web article by URL.

    Covers all artifact kinds: user | rule | article | note. Dedup behavior:
    - source_url provided → URL-keyed dedup: updates existing article if same URL
      exists, saves new article otherwise. Always sets decay_protected=True.
    - consolidation_enabled in config → Jaccard dedup: near-identical content
      (>0.9 score) is skipped; overlapping content is merged.
    - else → straight create.

    Returns a dict with:
    - display: confirmation message — show directly to the user
    - artifact_id: assigned artifact ID
    - action: "saved", "merged", "appended", or "skipped"

    Args:
        content: Primary text of the artifact.
        artifact_kind: One of user | rule | article | note.
        title: Optional human-readable label.
        description: Optional ≤200-char hook used for retrieval ranking.
        source_url: Source URL for web-fetched articles. Triggers URL-keyed dedup.
        decay_protected: Protect from automatic decay. Always True when source_url is set.
    """
    valid_kinds = {e.value for e in ArtifactKindEnum}
    if artifact_kind not in valid_kinds:
        return tool_error(
            f"Unknown artifact_kind: {artifact_kind!r}. Valid values: {sorted(valid_kinds)}",
            ctx=ctx,
        )
    if source_url is not None and artifact_kind != ArtifactKindEnum.ARTICLE.value:
        return tool_error(
            f"source_url requires artifact_kind={ArtifactKindEnum.ARTICLE.value!r}, "
            f"got {artifact_kind!r}",
            ctx=ctx,
        )

    knowledge_dir = ctx.deps.knowledge_dir

    with _TRACER.start_as_current_span("co.knowledge.memory_create") as span:
        span.set_attribute("knowledge.artifact_kind", artifact_kind)
        span.set_attribute("knowledge.has_source_url", source_url is not None)

        result = save_artifact(
            knowledge_dir,
            content=content,
            artifact_kind=artifact_kind,
            title=title,
            description=description,
            source_url=source_url,
            decay_protected=decay_protected,
            consolidation_enabled=ctx.deps.config.knowledge.consolidation_enabled,
            consolidation_similarity_threshold=ctx.deps.config.knowledge.consolidation_similarity_threshold,
            memory_store=ctx.deps.memory_store,
        )
        span.set_attribute("knowledge.action", result.action)
        if result.action != "skipped" and ctx.deps.memory_store is not None:
            reindex(
                ctx.deps.memory_store,
                result.path,
                result.content,
                result.markdown_content,
                result.frontmatter_dict,
                result.filename_stem,
                chunk_size=ctx.deps.config.knowledge.chunk_size,
                chunk_overlap=ctx.deps.config.knowledge.chunk_overlap,
            )

    if result.action == "skipped":
        return tool_output(
            f"Skipped (near-identical to {result.path.name})",
            ctx=ctx,
            action=result.action,
            path=str(result.path),
            artifact_id=result.artifact_id,
        )

    action_label = {
        "saved": "Saved",
        "merged": "Updated",
        "appended": "Appended to",
    }.get(result.action, result.action.capitalize())

    return tool_output(
        f"✓ {action_label} artifact: {result.path.name}",
        ctx=ctx,
        action=result.action,
        path=str(result.path),
        artifact_id=result.artifact_id,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    approval=True,
    is_concurrent_safe=True,
    retries=1,
)
async def memory_modify(
    ctx: RunContext[CoDeps],
    filename_stem: str,
    action: Literal["append", "replace"],
    content: str,
    target: str = "",
) -> ToolReturn:
    """Append content to or surgically replace a passage in a saved artifact.

    Use memory_search to find the filename_stem, then call this tool.

    action="append" — adds content as a new line at the end of the body.
    action="replace" — replaces an exact passage (target) with content.
                       target must appear exactly once. Empty target is rejected.

    Guards applied before any I/O:
    - Rejects content / target containing Read-tool line-number prefixes
      (``1→ `` or ``Line N: ``).
    - For replace: target must appear exactly once (zero or multiple → error).

    Returns a dict with:
    - display: confirmation message — show directly to the user
    - filename_stem: artifact filename stem that was modified

    Args:
        filename_stem: Full file stem from memory_search (e.g. "003-user-prefers-pytest").
        action: "append" to add at the end, or "replace" to substitute an exact passage.
        content: Text to append, or replacement text for replace.
        target: For action="replace" — exact passage to replace (must appear exactly once).
    """
    knowledge_dir = ctx.deps.knowledge_dir

    try:
        async with ctx.deps.resource_locks.try_acquire(filename_stem):
            with _TRACER.start_as_current_span("co.knowledge.memory_modify") as span:
                span.set_attribute("knowledge.filename_stem", filename_stem)
                span.set_attribute("knowledge.action", action)

                result = mutate_artifact(
                    knowledge_dir,
                    filename_stem=filename_stem,
                    action=action,
                    content=content,
                    target=target,
                )
                if ctx.deps.memory_store is not None:
                    reindex(
                        ctx.deps.memory_store,
                        result.path,
                        result.updated_body,
                        result.markdown_content,
                        result.frontmatter,
                        result.filename_stem,
                        chunk_size=ctx.deps.config.knowledge.chunk_size,
                        chunk_overlap=ctx.deps.config.knowledge.chunk_overlap,
                    )

            return tool_output(
                f"✓ {result.action.capitalize()} artifact '{filename_stem}'.",
                ctx=ctx,
                filename_stem=filename_stem,
                action=result.action,
            )
    except ResourceBusyError:
        return tool_error(
            f"Artifact '{filename_stem}' is being modified by another tool call — retry next turn",
            ctx=ctx,
        )
    except FileNotFoundError as exc:
        return tool_error(str(exc), ctx=ctx)
    except ValueError as exc:
        return tool_error(str(exc), ctx=ctx)
