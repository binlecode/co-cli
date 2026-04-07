"""Memory save agent — singleton dedup agent for upsert routing.

Module-level Agent singleton with structured output. Receives a candidate
memory + manifest of existing memories and returns SAVE_NEW or UPDATE(slug).
Same pattern as _extraction_agent (memory/_extractor.py) and _summarizer_agent
(context/_summarization.py).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ToolReturn

from co_cli._model_factory import ResolvedModel
from co_cli.knowledge._frontmatter import parse_frontmatter
from co_cli.tools.tool_output import tool_output

logger = logging.getLogger(__name__)

_SAVE_PROMPT_PATH = Path(__file__).parent / "prompts" / "memory_save.md"

PAGE_SIZE = 100


class SaveResult(BaseModel):
    """Structured output from the memory save agent."""

    action: Literal["SAVE_NEW", "UPDATE"]
    target_slug: str | None = None


_memory_save_agent: Agent[None, SaveResult] = Agent(
    output_type=SaveResult,
    instructions=_SAVE_PROMPT_PATH.read_text(encoding="utf-8").strip(),
    retries=0,
    output_retries=0,
)


def build_memory_manifest(memories: list[Any]) -> str:
    """Build a one-line-per-entry manifest of existing memories.

    Args:
        memories: Pre-sorted list of MemoryEntry objects (caller controls order).

    Returns:
        Manifest string with one line per memory, capped at PAGE_SIZE entries.
    """
    lines: list[str] = []
    for entry in memories[:PAGE_SIZE]:
        ts = entry.updated or entry.created
        snippet = entry.content[:80].replace("\n", " ")
        tags_str = ", ".join(entry.tags) if entry.tags else ""
        slug = entry.path.stem
        lines.append(f"- {slug} ({ts}): {snippet}  [{tags_str}]")
    return "\n".join(lines)


async def check_and_save(
    content: str,
    manifest: str,
    resolved: ResolvedModel,
    timeout_seconds: float = 15.0,
) -> SaveResult:
    """Run the memory save agent to decide SAVE_NEW or UPDATE.

    Args:
        content: Candidate memory content.
        manifest: Formatted manifest of existing memories.
        resolved: Pre-built model + settings (ROLE_SUMMARIZATION).
        timeout_seconds: Per-call timeout.

    Returns:
        SaveResult with action and optional target_slug.
        Falls back to SAVE_NEW on any error.
    """
    import asyncio

    user_prompt = (
        f"Candidate memory:\n{content}\n\n"
        f"Existing memories:\n{manifest}"
    )

    try:
        coro = _memory_save_agent.run(
            user_prompt, model=resolved.model, model_settings=resolved.settings,
        )
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        return result.output
    except asyncio.TimeoutError:
        logger.info("memory save agent timed out, falling back to SAVE_NEW")
        return SaveResult(action="SAVE_NEW")
    except Exception:
        logger.debug("memory save agent failed, falling back to SAVE_NEW", exc_info=True)
        return SaveResult(action="SAVE_NEW")


def overwrite_memory(
    memory_dir: Path,
    target_slug: str,
    new_content: str,
    new_tags: list[str],
    auto_save_tags: list[str],
    knowledge_store: Any | None = None,
) -> ToolReturn | None:
    """Overwrite an existing memory file with new content (full body replace).

    Preserves created timestamp, merges tags, refreshes provenance/category/certainty.
    Re-indexes in FTS when knowledge_store is available.

    Args:
        memory_dir: Path to the memory directory.
        target_slug: Filename stem of the memory to overwrite.
        new_content: New body content (replaces old body entirely).
        new_tags: Tags from the candidate memory.
        auto_save_tags: Auto-save tag list from config.
        knowledge_store: Optional KnowledgeStore for FTS re-indexing.

    Returns:
        ToolReturn with consolidated action metadata.
    """
    from co_cli.tools.memory import (
        slugify,
        _classify_certainty,
        _detect_provenance,
        _detect_category,
    )

    # Sanitize slug: reject path separators to prevent directory traversal
    if "/" in target_slug or "\\" in target_slug or ".." in target_slug:
        logger.warning(f"overwrite_memory: invalid slug {target_slug!r}")
        return None
    target_path = memory_dir / f"{target_slug}.md"
    if not target_path.exists():
        logger.warning(f"overwrite_memory: target {target_slug} not found, falling through to SAVE_NEW")
        return None

    raw = target_path.read_text(encoding="utf-8")
    existing_fm, _ = parse_frontmatter(raw)

    # Merge tags (union)
    existing_tags = existing_fm.get("tags", [])
    merged_tags = list(set(existing_tags + (new_tags or [])))

    # Refresh frontmatter
    existing_fm["updated"] = datetime.now(timezone.utc).isoformat()
    existing_fm["tags"] = merged_tags
    existing_fm["provenance"] = _detect_provenance(merged_tags, auto_save_tags)
    existing_fm["auto_category"] = _detect_category(merged_tags)
    existing_fm["certainty"] = _classify_certainty(new_content)
    existing_fm.setdefault("kind", "memory")

    md_content = (
        f"---\n{yaml.dump(existing_fm, default_flow_style=False)}---\n\n"
        f"{new_content.strip()}\n"
    )
    target_path.write_text(md_content, encoding="utf-8")

    logger.info(f"Overwrite memory {existing_fm.get('id', target_slug)} (upsert UPDATE)")

    # FTS re-index
    if knowledge_store is not None:
        try:
            import hashlib as _hashlib
            knowledge_store.index(
                source="memory",
                kind="memory",
                path=str(target_path),
                title=slugify(new_content[:50]),
                content=new_content.strip(),
                mtime=target_path.stat().st_mtime,
                hash=_hashlib.sha256(md_content.encode()).hexdigest(),
                tags=" ".join(merged_tags),
                category=existing_fm.get("auto_category"),
                created=existing_fm.get("created"),
                updated=existing_fm.get("updated"),
            )
        except Exception as e:
            logger.warning(f"Failed to reindex memory {target_slug}: {e}")

    return tool_output(
        f"✓ Updated memory {existing_fm.get('id', target_slug)} (upsert)\n"
        f"Location: {target_path}",
        path=str(target_path),
        memory_id=existing_fm.get("id", target_slug),
        action="consolidated",
    )
