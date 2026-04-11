"""Memory lifecycle — write entrypoint for all memory save paths.

Handles write and retention enforcement.
Both the explicit save_memory tool and the auto-signal save path route
through persist_memory().
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from opentelemetry import trace as otel_trace
from pydantic_ai.messages import ToolReturn
from pydantic_ai.settings import ModelSettings

from co_cli.deps import CoDeps
from co_cli.knowledge._frontmatter import ArtifactTypeEnum
from co_cli.tools.tool_output import tool_output_raw

_TRACER = otel_trace.get_tracer("co.memory")
logger = logging.getLogger(__name__)


async def persist_memory(
    deps: CoDeps,
    content: str,
    tags: list[str] | None,
    related: list[str] | None,
    on_failure: Literal["add", "skip"] = "add",
    model: Any = None,
    model_settings: ModelSettings | None = None,
    artifact_type: str | None = None,
    always_on: bool = False,
    type_value: str | None = None,
    description_value: str | None = None,
) -> ToolReturn:
    """Write a memory through the full lifecycle: upsert → write.

    Entry point for all write paths (explicit save_memory tool and
    auto-signal save). When a memory save agent is available (model is not None),
    persist_memory acts as an upsert — checking existing memories and routing
    to create or update transparently.

    Args:
        deps: CoDeps with memory config scalars.
        content: Memory text in third person.
        tags: Categorization tags.
        related: Slugs of related memories for knowledge linking.
        on_failure: Behavior on resource conflict. "add" = raise ResourceBusyError
                    (explicit save path, model retries). "skip" = drop write silently
                    (auto-signal path).
        model: pydantic-ai model object for the memory save agent. When None,
               upsert check is skipped (direct write).
        model_settings: ModelSettings for inference (e.g. NOREASON_SETTINGS).

    Returns:
        ToolReturn with display, path, memory_id, action metadata keys.
    """
    # Must stay as a local import inside the function body — module-level import
    # creates a circular dependency between _lifecycle.py and memory.py.
    from co_cli.tools.memory import slugify

    memory_dir = deps.memory_dir
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Write-strict: reject unknown artifact_type values before writing
    if artifact_type is not None:
        valid_artifact_types = {e.value for e in ArtifactTypeEnum}
        if artifact_type not in valid_artifact_types:
            raise ValueError(
                f"persist_memory: unknown artifact_type {artifact_type!r}. "
                f"Valid values: {sorted(valid_artifact_types)}"
            )

    with _TRACER.start_as_current_span("co.memory.write"):
        return await _write_memory(
            deps,
            content,
            tags,
            related,
            model,
            model_settings,
            artifact_type,
            always_on,
            memory_dir,
            on_failure,
            slugify,
            type_value,
            description_value,
        )


async def _write_memory(
    deps: CoDeps,
    content: str,
    tags: list[str] | None,
    related: list[str] | None,
    model: Any,
    model_settings: ModelSettings | None,
    artifact_type: str | None,
    always_on: bool,
    memory_dir: Path,
    on_failure: Literal["add", "skip"],
    slugify: Any,
    type_value: str | None,
    description_value: str | None,
) -> ToolReturn:
    """Upsert check + per-file locked write."""
    from co_cli.tools.memory import load_memories
    from co_cli.tools.resource_lock import ResourceBusyError

    # Upsert check: when a model is available, consult the memory save agent
    # to decide create vs update.
    if model is not None:
        from co_cli.memory._save import build_memory_manifest, check_and_save, overwrite_memory

        memories = load_memories(memory_dir, kind="memory")
        memories.sort(key=lambda m: m.updated or m.created, reverse=True)
        manifest = build_memory_manifest(memories)

        if manifest:
            save_result = await check_and_save(content, manifest, model, model_settings)
            if save_result.action == "UPDATE" and save_result.target_slug:
                target_path = str(memory_dir / f"{save_result.target_slug}.md")
                try:
                    async with deps.resource_locks.try_acquire(target_path):
                        norm_tags = [t.lower() for t in tags] if tags else []
                        update_result = overwrite_memory(
                            memory_dir,
                            save_result.target_slug,
                            content,
                            norm_tags,
                            deps.config.memory.auto_save_tags,
                            new_type=type_value,
                            new_description=description_value,
                        )
                except ResourceBusyError:
                    if on_failure == "skip":
                        logger.info("persist_memory: target file busy, skipping")
                        return tool_output_raw(
                            "⚠ Memory save skipped (file busy)", action="skipped"
                        )
                    raise
                # None = slug invalid or not found; fall through to SAVE_NEW
                if update_result is not None:
                    return update_result

    # SAVE_NEW: create new memory file
    import uuid as _uuid

    memory_id = str(_uuid.uuid4())
    slug = slugify(content[:50])
    filename = f"{slug}.md"
    file_path = memory_dir / filename

    # Normalize tags to lowercase so detection functions match consistently
    tags = [t.lower() for t in tags] if tags else []

    frontmatter: dict[str, Any] = {
        "id": memory_id,
        "kind": "memory",
        "created": datetime.now(UTC).isoformat(),
        "tags": tags,
    }
    if type_value is not None:
        frontmatter["type"] = type_value
    if description_value is not None:
        frontmatter["description"] = description_value
    if related:
        frontmatter["related"] = related
    if artifact_type is not None:
        frontmatter["artifact_type"] = artifact_type
    if always_on:
        frontmatter["always_on"] = True

    md_content = (
        f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{content.strip()}\n"
    )

    try:
        async with deps.resource_locks.try_acquire(str(file_path)):
            file_path.write_text(md_content, encoding="utf-8")
            logger.info(f"Saved memory {memory_id} to {file_path}")
    except ResourceBusyError:
        if on_failure == "skip":
            logger.info("persist_memory: new file busy, skipping")
            return tool_output_raw("⚠ Memory save skipped (file busy)", action="skipped")
        raise

    return tool_output_raw(
        f"✓ Saved memory {memory_id}: {filename}\nLocation: {file_path}",
        path=str(file_path),
        memory_id=memory_id,
        action="saved",
    )
