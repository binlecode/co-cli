"""Memory lifecycle — write entrypoint for all memory save paths.

Handles dedup fast-path, write, FTS indexing, and retention enforcement.
Both the explicit save_memory tool and the auto-signal save path route
through persist_memory().
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from opentelemetry import trace as otel_trace

from co_cli._model_factory import ResolvedModel
from co_cli.knowledge._frontmatter import parse_frontmatter
from co_cli.deps import CoDeps
from co_cli.memory._retention import enforce_retention
from co_cli.tools._result import ToolResult, make_result

_TRACER = otel_trace.get_tracer("co.memory")
logger = logging.getLogger(__name__)


def apply_plan_atomically(
    plan: Any,
    alias_map: dict[str, Any],
    new_content: str,
) -> None:
    """Apply a ConsolidationPlan against resolved MemoryEntry objects.

    Resolves alias strings to MemoryEntry objects, then applies ADD/UPDATE/DELETE
    actions in order. DELETE on a decay_protected entry is silently skipped.

    Args:
        plan: ConsolidationPlan with action decisions.
        alias_map: Dict mapping alias strings ("M1"...) to MemoryEntry objects.
        new_content: The incoming content that triggered consolidation; used as the
            replacement body for UPDATE actions.
    """
    from co_cli.tools.memory import _update_existing_memory

    for action in plan.actions:
        if action.action == "UPDATE":
            if action.target_alias and action.target_alias in alias_map:
                entry = alias_map[action.target_alias]
                try:
                    _update_existing_memory(entry, new_content, entry.tags)
                except Exception as e:
                    logger.warning(f"apply_plan_atomically UPDATE failed for {action.target_alias}: {e}")
        elif action.action == "DELETE":
            if action.target_alias and action.target_alias in alias_map:
                entry = alias_map[action.target_alias]
                if entry.decay_protected:
                    logger.debug(
                        f"apply_plan_atomically: skipping DELETE on protected entry {entry.id}"
                    )
                    continue
                try:
                    entry.path.unlink()
                    logger.info(f"apply_plan_atomically: deleted memory {entry.id}")
                except Exception as e:
                    logger.warning(f"apply_plan_atomically DELETE failed for {entry.id}: {e}")


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
) -> ToolResult:
    """Write a memory through the full lifecycle: dedup → consolidate → write → retention.

    Entry point for all write paths (explicit save_memory tool and
    auto-signal save). Both callers share the same dedup and retention logic.

    Args:
        deps: CoDeps with memory config scalars.
        content: Memory text in third person.
        tags: Categorization tags.
        related: Slugs of related memories for knowledge linking.
        provenance: Override provenance value. When None, auto-detected from tags.
        title: Override filename stem. When provided, dedup is skipped.
        on_failure: Behavior when consolidation fails. "add" = safe fallback to ADD
                    (explicit save path). "skip" = drop write on failure (auto-signal path).
        resolved: Pre-built model + settings for consolidation. When None or model is None,
                  consolidation is skipped.

    Returns:
        dict with display, path, memory_id, action keys.
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
) -> dict[str, Any]:
    # Import here to avoid module-level circular import
    from co_cli.tools.memory import (
        _load_memories,
        _check_duplicate,
        _update_existing_memory,
        _slugify,
        _classify_certainty,
        _detect_provenance,
        _detect_category,
        _parse_created,
    )

    memory_dir = deps.config.memory_dir
    memory_dir.mkdir(parents=True, exist_ok=True)

    # For next-id: must include all items (memories + articles share the ID sequence)
    all_items_for_id = _load_memories(memory_dir)
    # For dedup/consolidation candidates: memories only
    memories = _load_memories(memory_dir, kind="memory")

    # When title is provided (e.g. session checkpoints), skip dedup — the
    # filename is already unique by design (timestamp-based).
    if title is None:
        # Step 1: Check for duplicates in recent memories
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=deps.config.memory_dedup_window_days
        )
        recent = sorted(
            [m for m in memories if _parse_created(m.created) >= cutoff],
            key=lambda m: m.created,
            reverse=True,
        )[:10]

        is_dup, match, similarity = _check_duplicate(
            content, recent, threshold=deps.config.memory_dedup_threshold
        )

        # Step 2: If duplicate found, update existing memory
        if is_dup and match is not None:
            logger.info(
                f"Duplicate detected (similarity: {similarity:.1f}%) "
                f"- updating memory {match.id}"
            )
            result = _update_existing_memory(match, content, tags)
            result["similarity"] = similarity
            if deps.services.knowledge_index is not None:
                try:
                    import hashlib as _hashlib
                    raw = match.path.read_text(encoding="utf-8")
                    fm, body = parse_frontmatter(raw)
                    file_hash = _hashlib.sha256(raw.encode()).hexdigest()
                    entry_kind = fm.get("kind", "memory")
                    entry_source = "library" if entry_kind == "article" else "memory"
                    deps.services.knowledge_index.index(
                        source=entry_source,
                        kind=entry_kind,
                        path=str(match.path),
                        title=_slugify(content[:50]),
                        content=body.strip(),
                        mtime=match.path.stat().st_mtime,
                        hash=file_hash,
                        tags=" ".join(fm.get("tags", [])),
                        category=fm.get("auto_category"),
                        created=fm.get("created"),
                        updated=fm.get("updated"),
                    )
                except Exception as e:
                    logger.warning(f"Failed to reindex consolidated memory {match.id}: {e}")
            return result

    # Step 2b: LLM consolidation (when resolved model is available)
    if resolved is not None and resolved.model is not None:
        try:
            from co_cli.memory._consolidator import consolidate

            timeout = deps.config.memory_consolidation_timeout_seconds
            candidates = sorted(memories, key=lambda m: m.created, reverse=True)[:deps.config.memory_consolidation_top_k]
            alias_map = {f"M{i+1}": entry for i, entry in enumerate(candidates)}
            plan = await consolidate(content, candidates, resolved, timeout_seconds=timeout)
            apply_plan_atomically(plan, alias_map, content)

            has_add = any(a.action == "ADD" for a in plan.actions)
            # Only exit early when there are non-ADD actions (MERGEs): content was
            # absorbed into an existing memory — no new entry needed.
            # Empty plan (0 actions) means the LLM had no opinion; fall through to
            # Step 3 so the memory is written rather than silently dropped.
            if not has_add and plan.actions:
                return make_result(
                    "\u2713 Memory consolidated (no new entry needed)",
                    action="consolidated",
                    memory_id=None,
                )
        except asyncio.TimeoutError:
            if on_failure == "skip":
                logger.info("persist_memory: consolidation timeout, skipping (auto-signal path)")
                return make_result(
                    "\u26a0 Memory save skipped (consolidation timeout)",
                    action="skipped",
                )
            logger.info("persist_memory: consolidation timeout, falling back to ADD")

    # Step 3: No duplicate — create new memory
    max_id = max((m.id for m in all_items_for_id), default=0)
    memory_id = max_id + 1
    filename = f"{title}.md" if title else f"{memory_id:03d}-{_slugify(content[:50])}.md"

    # Normalize tags to lowercase so detection functions match consistently
    tags = [t.lower() for t in tags] if tags else []

    frontmatter: dict[str, Any] = {
        "id": memory_id,
        "kind": "memory",
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": tags,
        "provenance": provenance if provenance is not None else _detect_provenance(tags),
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

    file_path = memory_dir / filename
    file_path.write_text(md_content, encoding="utf-8")

    logger.info(f"Saved memory {memory_id} to {file_path}")

    # FTS index integration — no-op when knowledge_index is None
    if deps.services.knowledge_index is not None:
        try:
            import hashlib as _hashlib
            deps.services.knowledge_index.index(
                source="memory",
                kind="memory",
                path=str(file_path),
                title=_slugify(content[:50]),
                content=content.strip(),
                mtime=file_path.stat().st_mtime,
                hash=_hashlib.sha256(md_content.encode()).hexdigest(),
                tags=" ".join(tags or []),
                category=_detect_category(tags),
                created=frontmatter["created"],
            )
        except Exception as e:
            logger.warning(f"Failed to index memory {memory_id}: {e}")

    result: ToolResult = make_result(
        f"✓ Saved memory {memory_id}: {filename}\n"
        f"Location: {file_path}",
        path=str(file_path),
        memory_id=memory_id,
        action="saved",
    )

    # Step 4: Retention cap — trigger if strictly exceeded (memories only;
    # articles are decay_protected and must not be evicted by memory pressure)
    all_memories = _load_memories(memory_dir, kind="memory")
    total_count = len(all_memories)

    if total_count > deps.config.memory_max_count:
        logger.info(
            f"Memory limit exceeded ({total_count}/{deps.config.memory_max_count}) "
            f"- triggering retention cut"
        )
        decay_result = await enforce_retention(deps, all_memories)
        result["decay_triggered"] = True
        result["decay_count"] = decay_result["decayed"]
        result["decay_strategy"] = decay_result["strategy"]
        result["display"] += (
            f"\n♻️ Decayed {decay_result['decayed']} old memories "
            f"({decay_result['strategy']})"
        )

        # FTS: remove stale entries for deleted files
        if deps.services.knowledge_index is not None:
            try:
                current_paths = {str(p) for p in memory_dir.rglob("*.md")}
                deps.services.knowledge_index.remove_stale(
                    "memory", current_paths, directory=memory_dir
                )
            except Exception as e:
                logger.warning(f"Failed to remove stale FTS entries: {e}")

    return result
