"""Memory management tool — `knowledge_manage` for create, append, replace, and delete actions."""

import logging
from typing import Literal

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import ApprovalKindEnum, ApprovalSubject, CoDeps, VisibilityPolicyEnum
from co_cli.memory.artifact import ArtifactKindEnum
from co_cli.memory.service import mutate_artifact, reindex, save_artifact
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.resource_lock import ResourceBusyError
from co_cli.tools.tool_io import tool_error, tool_output

_TRACER = otel_trace.get_tracer("co.knowledge")

logger = logging.getLogger(__name__)


def _knowledge_manage_approval_subject(args: dict) -> ApprovalSubject:
    action = args.get("action", "unknown")
    name = args.get("name", "unknown")
    return ApprovalSubject(
        tool_name="knowledge_manage",
        kind=ApprovalKindEnum.TOOL,
        value=f"tool:knowledge_manage:{action}:{name}",
        display=f"knowledge_manage(action={action!r}, name={name!r})",
        can_remember=True,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    approval=True,
    approval_subject_fn=_knowledge_manage_approval_subject,
    is_concurrent_safe=True,
    retries=1,
)
async def knowledge_manage(
    ctx: RunContext[CoDeps],
    action: Literal["create", "append", "replace", "delete"],
    name: str,
    content: str | None = None,
    kind: str | None = None,
    section: str | None = None,
) -> ToolReturn:
    """Create, update, or delete a knowledge artifact.

    action='create'  — Save a new artifact. Requires content and kind.
                       Dedup behavior:
                       - name treated as title; source_url dedup not supported here.
                       - consolidation_enabled in config → Jaccard dedup.
    action='append'  — Add content at the end of an existing artifact body.
                       name must be the filename_stem (from memory_search).
    action='replace' — Surgically replace a passage in an existing artifact.
                       name must be the filename_stem; section is the exact passage
                       to replace (must appear exactly once); content is the replacement.
    action='delete'  — Remove the artifact file and its index entries.
                       name must be the filename_stem (from memory_search).

    Returns a dict with:
    - display: confirmation message — show directly to the user
    - action: the action that was performed

    Args:
        action: One of create | append | replace | delete.
        name: For create — the artifact title. For append/replace/delete — the filename_stem.
        content: Text body for create/append, or replacement text for replace.
        kind: Required for create. One of user | rule | article | note.
        section: For replace — the exact passage to replace (must appear exactly once).
    """
    if action == "create":
        return await _handle_create(ctx, name=name, content=content, kind=kind)
    if action == "append":
        return await _handle_mutate(ctx, filename_stem=name, op="append", content=content)
    if action == "replace":
        return await _handle_mutate(
            ctx, filename_stem=name, op="replace", content=content, target=section or ""
        )
    if action == "delete":
        return await _handle_delete(ctx, filename_stem=name)
    return tool_error(
        f"Unknown action: {action!r}. Valid values: create, append, replace, delete",
        ctx=ctx,
    )


async def _handle_create(
    ctx: RunContext[CoDeps],
    *,
    name: str,
    content: str | None,
    kind: str | None,
) -> ToolReturn:
    if content is None:
        return tool_error("content is required for action='create'", ctx=ctx)
    if kind is None:
        return tool_error("kind is required for action='create'", ctx=ctx)

    valid_kinds = {e.value for e in ArtifactKindEnum if e != ArtifactKindEnum.CANON}
    if kind not in valid_kinds:
        return tool_error(
            f"Unknown kind: {kind!r}. Valid values: {sorted(valid_kinds)}",
            ctx=ctx,
        )

    knowledge_dir = ctx.deps.knowledge_dir

    with _TRACER.start_as_current_span("co.knowledge.knowledge_manage.create") as span:
        span.set_attribute("knowledge.artifact_kind", kind)

        result = save_artifact(
            knowledge_dir,
            content=content,
            artifact_kind=kind,
            title=name,
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
                chunk_tokens=ctx.deps.config.knowledge.chunk_tokens,
                chunk_overlap_tokens=ctx.deps.config.knowledge.chunk_overlap_tokens,
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


async def _handle_mutate(
    ctx: RunContext[CoDeps],
    *,
    filename_stem: str,
    op: Literal["append", "replace"],
    content: str | None,
    target: str = "",
) -> ToolReturn:
    if content is None:
        return tool_error(f"content is required for action='{op}'", ctx=ctx)

    knowledge_dir = ctx.deps.knowledge_dir

    try:
        async with ctx.deps.resource_locks.try_acquire(filename_stem):
            with _TRACER.start_as_current_span("co.knowledge.knowledge_manage.mutate") as span:
                span.set_attribute("knowledge.filename_stem", filename_stem)
                span.set_attribute("knowledge.action", op)

                result = mutate_artifact(
                    knowledge_dir,
                    filename_stem=filename_stem,
                    action=op,
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
                        chunk_tokens=ctx.deps.config.knowledge.chunk_tokens,
                        chunk_overlap_tokens=ctx.deps.config.knowledge.chunk_overlap_tokens,
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


async def _handle_delete(
    ctx: RunContext[CoDeps],
    *,
    filename_stem: str,
) -> ToolReturn:
    knowledge_dir = ctx.deps.knowledge_dir
    artifact_path = knowledge_dir / f"{filename_stem}.md"

    if not artifact_path.exists():
        return tool_error(
            f"Artifact '{filename_stem}' not found — verify the filename_stem via memory_search",
            ctx=ctx,
        )

    with _TRACER.start_as_current_span("co.knowledge.knowledge_manage.delete") as span:
        span.set_attribute("knowledge.filename_stem", filename_stem)

        artifact_path.unlink()

        if ctx.deps.memory_store is not None:
            ctx.deps.memory_store.remove("knowledge", str(artifact_path))

    return tool_output(
        f"✓ Deleted artifact '{filename_stem}'.",
        ctx=ctx,
        filename_stem=filename_stem,
        action="deleted",
    )
