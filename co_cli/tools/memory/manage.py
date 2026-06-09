"""Memory write tools — `memory_create`, `memory_append`, `memory_replace`, `memory_delete`."""

import logging
from collections.abc import Callable
from typing import Literal

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import ApprovalKindEnum, ApprovalSubject, CoDeps, VisibilityPolicyEnum
from co_cli.memory.item import MemoryKind, MemoryKindEnum, SourceTypeEnum
from co_cli.memory.service import mutate_memory_item, reindex, save_memory_item
from co_cli.observability.tracing import current_span, trace
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.resource_lock import ResourceBusyError
from co_cli.tools.tool_io import tool_error, tool_output

logger = logging.getLogger(__name__)


def _subject_fn(tool_name: str, arg_key: str) -> Callable[[dict], ApprovalSubject]:
    """Build an approval-subject fn that keys on a single name-bearing arg."""

    def subject(args: dict) -> ApprovalSubject:
        name = args.get(arg_key, "unknown")
        return ApprovalSubject(
            tool_name=tool_name,
            kind=ApprovalKindEnum.TOOL,
            value=f"tool:{tool_name}:{name}",
            display=f"{tool_name}(name={name!r})",
            can_remember=True,
        )

    return subject


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_approval_required=True,
    approval_subject_fn=_subject_fn("memory_create", "name_title"),
    is_concurrent_safe=True,
    retries=1,
)
async def memory_create(
    ctx: RunContext[CoDeps],
    name_title: str,
    content: str,
    kind: MemoryKind,
    source_type: SourceTypeEnum = SourceTypeEnum.MANUAL,
    source_url: str | None = None,
) -> ToolReturn:
    """Save a new memory artifact.

    Args:
        name_title: Title for the new artifact (becomes its display name; the
            filename_stem is derived from it). Not a filename_stem — that exists
            only after the artifact is saved.
        content: The artifact body.
        kind: One of user | rule | article | note.
        source_type: Provenance tag (default 'manual').
        source_url: When set, enables URL-keyed dedup: a re-save with the same URL
            consolidates onto the existing article instead of duplicating. Default None.
    """
    return await _handle_create(
        ctx,
        name=name_title,
        content=content,
        kind=kind,
        source_type=source_type,
        source_url=source_url,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_approval_required=True,
    approval_subject_fn=_subject_fn("memory_append", "filename_stem"),
    is_concurrent_safe=True,
    retries=1,
)
async def memory_append(
    ctx: RunContext[CoDeps],
    filename_stem: str,
    content: str,
) -> ToolReturn:
    """Append content to the end of an existing memory artifact's body.

    Args:
        filename_stem: The filename_stem from a memory_search hit (not the title).
        content: Text to append to the end of the artifact body.
    """
    return await _handle_mutate(ctx, filename_stem=filename_stem, op="append", content=content)


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_approval_required=True,
    approval_subject_fn=_subject_fn("memory_replace", "filename_stem"),
    is_concurrent_safe=True,
    retries=1,
)
async def memory_replace(
    ctx: RunContext[CoDeps],
    filename_stem: str,
    section: str,
    content: str,
) -> ToolReturn:
    """Replace a passage in an existing memory artifact.

    Args:
        filename_stem: The filename_stem from a memory_search hit (not the title).
        section: The exact passage to replace; must appear exactly once in the body.
        content: The replacement text.
    """
    return await _handle_mutate(
        ctx, filename_stem=filename_stem, op="replace", content=content, target=section
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_approval_required=True,
    approval_subject_fn=_subject_fn("memory_delete", "filename_stem"),
    is_concurrent_safe=True,
    retries=1,
)
async def memory_delete(
    ctx: RunContext[CoDeps],
    filename_stem: str,
) -> ToolReturn:
    """Delete a memory artifact and its index entries.

    Args:
        filename_stem: The filename_stem from a memory_search hit (not the title).
    """
    return await _handle_delete(ctx, filename_stem=filename_stem)


@trace("co.memory.memory_create")
async def _handle_create(
    ctx: RunContext[CoDeps],
    *,
    name: str,
    content: str,
    kind: str,
    source_type: SourceTypeEnum = SourceTypeEnum.MANUAL,
    source_url: str | None = None,
) -> ToolReturn:
    valid_kinds = {e.value for e in MemoryKindEnum if e != MemoryKindEnum.CANON}
    if kind not in valid_kinds:
        return tool_error(
            f"Unknown kind: {kind!r}. Valid values: {sorted(valid_kinds)}",
            ctx=ctx,
        )

    memory_dir = ctx.deps.memory_dir

    span = current_span()
    span.set_attribute("memory.memory_kind", kind)

    result = save_memory_item(
        memory_dir,
        content=content,
        memory_kind=kind,
        title=name,
        source_type=str(source_type),
        source_url=source_url,
        consolidation_similarity_threshold=ctx.deps.config.memory.consolidation_similarity_threshold,
        index_store=ctx.deps.index_store,
    )
    span.set_attribute("memory.action", result.action)
    if result.action != "skipped" and ctx.deps.index_store is not None:
        reindex(
            ctx.deps.index_store,
            result.path,
            result.content,
            result.markdown_content,
            result.frontmatter_dict,
            result.filename_stem,
            chunk_tokens=ctx.deps.config.memory.chunk_tokens,
            chunk_overlap_tokens=ctx.deps.config.memory.chunk_overlap_tokens,
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

    ctx.deps.session.turns_since_memory_review = 0
    return tool_output(
        f"✓ {action_label} artifact: {result.path.name}",
        ctx=ctx,
        action=result.action,
        path=str(result.path),
        artifact_id=result.artifact_id,
    )


@trace("co.memory.memory_mutate")
async def _handle_mutate(
    ctx: RunContext[CoDeps],
    *,
    filename_stem: str,
    op: Literal["append", "replace"],
    content: str,
    target: str = "",
) -> ToolReturn:
    memory_dir = ctx.deps.memory_dir
    span = current_span()
    span.set_attribute("memory.filename_stem", filename_stem)
    span.set_attribute("memory.action", op)

    try:
        async with ctx.deps.resource_locks.try_acquire(filename_stem):
            result = mutate_memory_item(
                memory_dir,
                filename_stem=filename_stem,
                action=op,
                content=content,
                target=target,
            )
            if ctx.deps.index_store is not None:
                reindex(
                    ctx.deps.index_store,
                    result.path,
                    result.updated_body,
                    result.markdown_content,
                    result.frontmatter,
                    result.filename_stem,
                    chunk_tokens=ctx.deps.config.memory.chunk_tokens,
                    chunk_overlap_tokens=ctx.deps.config.memory.chunk_overlap_tokens,
                )

            ctx.deps.session.turns_since_memory_review = 0
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


@trace("co.memory.memory_delete")
async def _handle_delete(
    ctx: RunContext[CoDeps],
    *,
    filename_stem: str,
) -> ToolReturn:
    memory_dir = ctx.deps.memory_dir
    artifact_path = memory_dir / f"{filename_stem}.md"

    if not artifact_path.exists():
        return tool_error(
            f"Artifact '{filename_stem}' not found — verify the filename_stem via memory_search",
            ctx=ctx,
        )

    current_span().set_attribute("memory.filename_stem", filename_stem)
    artifact_path.unlink()

    if ctx.deps.memory_store is not None:
        ctx.deps.memory_store.remove(artifact_path)

    return tool_output(
        f"✓ Deleted artifact '{filename_stem}'.",
        ctx=ctx,
        filename_stem=filename_stem,
        action="deleted",
    )
