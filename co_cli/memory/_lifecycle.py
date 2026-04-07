"""Memory lifecycle — write entrypoint for all memory save paths.

Handles write, FTS indexing, and retention enforcement.
Both the explicit save_memory tool and the auto-signal save path route
through persist_memory().
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from opentelemetry import trace as otel_trace

from co_cli._model_factory import ResolvedModel
from co_cli.knowledge._frontmatter import ArtifactTypeEnum
from co_cli.deps import CoDeps
from co_cli.memory._retention import enforce_retention
from pydantic_ai.messages import ToolReturn
from co_cli.tools.tool_output import tool_output

_TRACER = otel_trace.get_tracer("co.memory")
logger = logging.getLogger(__name__)


async def persist_memory(
    deps: CoDeps,
    content: str,
    tags: list[str] | None,
    related: list[str] | None,
    provenance: str | None = None,
    title: str | None = None,
    on_failure: Literal["add", "skip"] = "add",
    resolved: ResolvedModel | None = None,
    artifact_type: str | None = None,
    always_on: bool = False,
) -> ToolReturn:
    """Write a memory through the full lifecycle: upsert → write → retention.

    Entry point for all write paths (explicit save_memory tool and
    auto-signal save). When a memory save agent is available (resolved model),
    persist_memory acts as an upsert — checking existing memories and routing
    to create or update transparently.

    Args:
        deps: CoDeps with memory config scalars.
        content: Memory text in third person.
        tags: Categorization tags.
        related: Slugs of related memories for knowledge linking.
        provenance: Override provenance value. When None, auto-detected from tags.
        title: Override filename stem. When provided, upsert check is skipped.
        on_failure: Behavior on resource conflict. "add" = raise ResourceBusyError
                    (explicit save path, model retries). "skip" = drop write silently
                    (auto-signal path).
        resolved: Pre-built model + settings for the memory save agent. When None
                  or model is None, upsert check is skipped (direct write).

    Returns:
        ToolReturn with display, path, memory_id, action metadata keys.
        May include decay_triggered, decay_count, decay_strategy keys.
    """
    with _TRACER.start_as_current_span("co.memory.write") as span:
        span.set_attribute("memory.provenance", provenance or "auto")
        return await _persist_memory_inner(deps, content, tags, related, provenance, title, on_failure, resolved, artifact_type, always_on)


async def _persist_memory_inner(
    deps: CoDeps,
    content: str,
    tags: list[str] | None,
    related: list[str] | None,
    provenance: str | None = None,
    title: str | None = None,
    on_failure: Literal["add", "skip"] = "add",
    resolved: ResolvedModel | None = None,
    artifact_type: str | None = None,
    always_on: bool = False,
) -> ToolReturn:
    # Import here to avoid module-level circular import
    from co_cli.tools.memory import (
        load_memories,
        slugify,
        _classify_certainty,
        _detect_provenance,
        _detect_category,
    )

    memory_dir = deps.config.memory_dir
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Write-strict: reject unknown artifact_type values before writing
    if artifact_type is not None:
        valid_artifact_types = {e.value for e in ArtifactTypeEnum}
        if artifact_type not in valid_artifact_types:
            raise ValueError(
                f"persist_memory: unknown artifact_type {artifact_type!r}. "
                f"Valid values: {sorted(valid_artifact_types)}"
            )

    result = await _write_memory(
        deps, content, tags, related, provenance, title,
        resolved, artifact_type, always_on,
        memory_dir, on_failure, slugify, _classify_certainty,
        _detect_provenance, _detect_category,
    )

    # Retention cap — runs outside the lock (idempotent, no read-modify-write race)
    all_memories = load_memories(memory_dir, kind="memory")
    total_count = len(all_memories)

    if total_count > deps.config.memory_max_count:
        logger.info(
            f"Memory limit exceeded ({total_count}/{deps.config.memory_max_count}) "
            f"- triggering retention cut"
        )
        decay_result = await enforce_retention(deps, all_memories)
        result.metadata["decay_triggered"] = True
        result.metadata["decay_count"] = decay_result["decayed"]
        result.metadata["decay_strategy"] = decay_result["strategy"]
        result.return_value += (
            f"\n♻️ Decayed {decay_result['decayed']} old memories "
            f"({decay_result['strategy']})"
        )

        # FTS: remove stale entries for deleted files
        if deps.knowledge_store is not None:
            try:
                current_paths = {str(p) for p in memory_dir.rglob("*.md")}
                deps.knowledge_store.remove_stale(
                    "memory", current_paths, directory=memory_dir
                )
            except Exception as e:
                logger.warning(f"Failed to remove stale FTS entries: {e}")

    return result


async def _write_memory(
    deps: CoDeps,
    content: str,
    tags: list[str] | None,
    related: list[str] | None,
    provenance: str | None,
    title: str | None,
    resolved: ResolvedModel | None,
    artifact_type: str | None,
    always_on: bool,
    memory_dir: Path,
    on_failure: Literal["add", "skip"],
    slugify: Any,
    _classify_certainty: Any,
    _detect_provenance: Any,
    _detect_category: Any,
) -> ToolReturn:
    """Upsert check + per-file locked write."""
    from co_cli.tools.memory import load_memories
    from co_cli.tools._resource_lock import ResourceBusyError

    # Upsert check: when a model is available and title is not preset,
    # consult the memory save agent to decide create vs update.
    if title is None and resolved is not None and resolved.model is not None:
        from co_cli.memory._save import build_memory_manifest, check_and_save, overwrite_memory

        memories = load_memories(memory_dir, kind="memory")
        memories.sort(key=lambda m: m.updated or m.created, reverse=True)
        manifest = build_memory_manifest(memories)

        if manifest:
            save_result = await check_and_save(content, manifest, resolved)
            if save_result.action == "UPDATE" and save_result.target_slug:
                target_path = str(memory_dir / f"{save_result.target_slug}.md")
                try:
                    async with deps.resource_locks.try_acquire(target_path):
                        norm_tags = [t.lower() for t in tags] if tags else []
                        update_result = overwrite_memory(
                            memory_dir, save_result.target_slug, content,
                            norm_tags, deps.config.memory_auto_save_tags,
                            knowledge_store=deps.knowledge_store,
                        )
                except ResourceBusyError:
                    if on_failure == "skip":
                        logger.info("persist_memory: target file busy, skipping")
                        return tool_output("⚠ Memory save skipped (file busy)", action="skipped")
                    raise
                # None = slug invalid or not found; fall through to SAVE_NEW
                if update_result is not None:
                    return update_result

    # SAVE_NEW: create new memory file
    import uuid as _uuid
    memory_id = str(_uuid.uuid4())
    slug = slugify(content[:50])
    filename = f"{title}.md" if title else f"{slug}-{memory_id[:6]}.md"
    file_path = memory_dir / filename

    # Normalize tags to lowercase so detection functions match consistently
    tags = [t.lower() for t in tags] if tags else []

    frontmatter: dict[str, Any] = {
        "id": memory_id,
        "kind": "memory",
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": tags,
        "provenance": provenance if provenance is not None else _detect_provenance(tags, deps.config.memory_auto_save_tags),
        "auto_category": _detect_category(tags),
        "certainty": _classify_certainty(content),
    }
    if related:
        frontmatter["related"] = related
    if artifact_type is not None:
        frontmatter["artifact_type"] = artifact_type
    if always_on:
        frontmatter["always_on"] = True

    md_content = (
        f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n"
        f"{content.strip()}\n"
    )

    try:
        async with deps.resource_locks.try_acquire(str(file_path)):
            file_path.write_text(md_content, encoding="utf-8")
            logger.info(f"Saved memory {memory_id} to {file_path}")

            # FTS index integration — no-op when knowledge_store is None
            if deps.knowledge_store is not None:
                try:
                    import hashlib as _hashlib
                    deps.knowledge_store.index(
                        source="memory",
                        kind="memory",
                        path=str(file_path),
                        title=slugify(content[:50]),
                        content=content.strip(),
                        mtime=file_path.stat().st_mtime,
                        hash=_hashlib.sha256(md_content.encode()).hexdigest(),
                        tags=" ".join(tags or []),
                        category=_detect_category(tags),
                        created=frontmatter["created"],
                    )
                except Exception as e:
                    logger.warning(f"Failed to index memory {memory_id}: {e}")
    except ResourceBusyError:
        if on_failure == "skip":
            logger.info("persist_memory: new file busy, skipping")
            return tool_output("⚠ Memory save skipped (file busy)", action="skipped")
        raise

    return tool_output(
        f"✓ Saved memory {memory_id}: {filename}\n"
        f"Location: {file_path}",
        path=str(file_path),
        memory_id=memory_id,
        action="saved",
    )
