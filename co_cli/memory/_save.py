"""Memory save agent — singleton dedup agent for upsert routing.

Module-level Agent singleton with structured output. Receives a candidate
memory + manifest of existing memories and returns SAVE_NEW or UPDATE(slug).
Same pattern as _extraction_agent (memory/_extractor.py) and _summarizer_agent
(context/summarization.py).
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ToolReturn
from pydantic_ai.settings import ModelSettings

from co_cli.knowledge._frontmatter import parse_frontmatter
from co_cli.memory.recall import MemoryEntry
from co_cli.tools.tool_output import tool_output_raw

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


def build_memory_manifest(memories: list[MemoryEntry]) -> str:
    """Build a one-line-per-entry manifest of existing memories.

    Args:
        memories: Pre-sorted list of MemoryEntry objects (caller controls order).

    Returns:
        Manifest string with one line per memory, capped at PAGE_SIZE entries.
        Format: - [type] slug (ts): description
    """
    lines: list[str] = []
    for entry in memories[:PAGE_SIZE]:
        ts = entry.updated or entry.created
        desc = entry.description or entry.content[:80].replace("\n", " ")
        slug = entry.path.stem
        lines.append(f"- [{entry.type or '?'}] {slug} ({ts}): {desc}")
    return "\n".join(lines)


async def check_and_save(
    content: str,
    manifest: str,
    model: Any,
    model_settings: ModelSettings | None = None,
    timeout_seconds: float = 15.0,
) -> SaveResult:
    """Run the memory save agent to decide SAVE_NEW or UPDATE.

    Args:
        content: Candidate memory content.
        manifest: Formatted manifest of existing memories.
        model: pydantic-ai model object for inference.
        model_settings: ModelSettings for inference (e.g. NOREASON_SETTINGS).
        timeout_seconds: Per-call timeout.

    Returns:
        SaveResult with action and optional target_slug.
        Falls back to SAVE_NEW on any error.
    """
    import asyncio

    user_prompt = f"Candidate memory:\n{content}\n\nExisting memories:\n{manifest}"

    try:
        coro = _memory_save_agent.run(
            user_prompt,
            model=model,
            model_settings=model_settings,
        )
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        return result.output
    except TimeoutError:
        logger.info("memory save agent timed out, falling back to SAVE_NEW")
        return SaveResult(action="SAVE_NEW")
    except Exception:
        logger.warning("memory save agent failed, falling back to SAVE_NEW", exc_info=True)
        return SaveResult(action="SAVE_NEW")


def overwrite_memory(
    memory_dir: Path,
    target_slug: str,
    content: str,
    tags: list[str],
    type_: str | None = None,
    description: str | None = None,
    name: str | None = None,
) -> ToolReturn | None:
    """Overwrite an existing memory file with new content (full body replace).

    Preserves created timestamp, merges tags.

    Args:
        memory_dir: Path to the memory directory.
        target_slug: Filename stem of the memory to overwrite.
        content: New body content (replaces old body entirely).
        tags: Tags from the candidate memory (merged with existing).
        type_: Optional taxonomy type (user/feedback/project/reference) to write to frontmatter.
        description: Optional purpose hook to write to frontmatter.
        name: Optional short identifier (≤60 chars) to write to frontmatter.

    Returns:
        ToolReturn with consolidated action metadata.
    """

    # Sanitize slug: reject path separators to prevent directory traversal
    if "/" in target_slug or "\\" in target_slug or ".." in target_slug:
        logger.warning(f"overwrite_memory: invalid slug {target_slug!r}")
        return None
    target_path = memory_dir / f"{target_slug}.md"
    if not target_path.exists():
        logger.warning(
            f"overwrite_memory: target {target_slug} not found, falling through to SAVE_NEW"
        )
        return None

    raw = target_path.read_text(encoding="utf-8")
    existing_fm, _ = parse_frontmatter(raw)

    # Merge tags (union)
    stored_tags = existing_fm.get("tags", [])
    merged_tags = list(set(stored_tags + (tags or [])))

    # Refresh frontmatter
    existing_fm["updated"] = datetime.now(UTC).isoformat()
    existing_fm["tags"] = merged_tags
    if type_ is not None:
        existing_fm["type"] = type_
    if name is not None:
        existing_fm["name"] = name
    if description is not None:
        existing_fm["description"] = description
    existing_fm.setdefault("kind", "memory")

    md_content = (
        f"---\n{yaml.dump(existing_fm, default_flow_style=False)}---\n\n{content.strip()}\n"
    )
    target_path.write_text(md_content, encoding="utf-8")

    logger.info(f"Overwrite memory {existing_fm.get('id', target_slug)} (upsert UPDATE)")

    return tool_output_raw(
        f"✓ Updated memory {existing_fm.get('id', target_slug)} (upsert)\nLocation: {target_path}",
        path=str(target_path),
        memory_id=existing_fm.get("id", target_slug),
        action="consolidated",
    )
