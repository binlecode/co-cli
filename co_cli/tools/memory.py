"""Memory management tools for persistent knowledge.

This module provides tools for saving, recalling, and listing memories in the
internal knowledge system. Memories are stored as markdown files with YAML
frontmatter in .co-cli/knowledge/memories/.

Retrieval uses grep-based search for MVP (<200 memories). Future phases will
add SQLite FTS5 and vector search as corpus grows.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz
from pydantic_ai import RunContext

from co_cli._frontmatter import parse_frontmatter, validate_memory_frontmatter
from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """In-memory representation of a loaded memory file."""

    id: int
    path: Path
    content: str
    tags: list[str]
    created: str  # ISO8601
    updated: str | None = None
    decay_protected: bool = False


# ---------------------------------------------------------------------------
# Single file scanner — everything else filters from this result
# ---------------------------------------------------------------------------


def _load_all_memories(memory_dir: Path) -> list[MemoryEntry]:
    """Load and validate all memory files from a directory.

    Returns a list of MemoryEntry objects. Invalid or malformed files are
    skipped with a warning.

    Args:
        memory_dir: Path to the memories directory

    Returns:
        List of validated MemoryEntry objects
    """
    if not memory_dir.exists():
        return []

    entries: list[MemoryEntry] = []
    for path in memory_dir.glob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)
            validate_memory_frontmatter(fm)
            entries.append(
                MemoryEntry(
                    id=fm["id"],
                    path=path,
                    content=body.strip(),
                    tags=fm.get("tags", []),
                    created=fm["created"],
                    updated=fm.get("updated"),
                    decay_protected=fm.get("decay_protected", False),
                )
            )
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
            continue
    return entries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug (max 50 chars).

    Args:
        text: Text to slugify

    Returns:
        Slugified text (lowercase, hyphens, no special chars)
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:50]


def _detect_source(tags: list[str] | None) -> str:
    """Detect if memory was auto-saved (detected) or explicitly requested (user-told).

    Args:
        tags: Tags list from save_memory call

    Returns:
        "detected" if signal tags present, "user-told" otherwise
    """
    if not tags:
        return "user-told"

    signal_tags = {"preference", "correction", "decision", "context", "pattern"}
    return "detected" if any(t in signal_tags for t in tags) else "user-told"


def _detect_category(tags: list[str] | None) -> str | None:
    """Extract primary category from tags.

    Args:
        tags: Tags list from save_memory call

    Returns:
        First matching category tag, or None if no category found
    """
    if not tags:
        return None

    categories = ["preference", "correction", "decision", "context", "pattern"]
    for category in categories:
        if category in tags:
            return category

    return None


def _parse_created(created_str: str) -> datetime:
    """Parse an ISO8601 created timestamp to a timezone-aware datetime."""
    return datetime.fromisoformat(created_str.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def _check_duplicate(
    new_content: str, recent_memories: list[MemoryEntry], threshold: int = 85
) -> tuple[bool, MemoryEntry | None, float]:
    """Check if new content is duplicate of existing memory using token-based similarity.

    Uses token_sort_ratio which handles word reordering better than plain ratio.
    For example: "I prefer TypeScript" vs "TypeScript I prefer" will score 100%.

    Args:
        new_content: Content to check for duplicates
        recent_memories: List of recent MemoryEntry objects to compare against
        threshold: Similarity threshold percentage (default 85)

    Returns:
        Tuple of (is_duplicate, matching_entry, similarity_score)
        - is_duplicate: True if similarity >= threshold
        - matching_entry: MemoryEntry of match (None if no duplicate)
        - similarity_score: Highest similarity score found (0-100)
    """
    if not recent_memories:
        return False, None, 0.0

    new_lower = new_content.lower().strip()
    max_similarity = 0.0
    best_match: MemoryEntry | None = None

    for candidate in recent_memories:
        candidate_lower = candidate.content.lower().strip()
        similarity = fuzz.token_sort_ratio(new_lower, candidate_lower)

        if similarity > max_similarity:
            max_similarity = similarity
            best_match = candidate

    is_duplicate = max_similarity >= threshold
    return is_duplicate, best_match if is_duplicate else None, max_similarity


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


def _update_existing_memory(
    entry: MemoryEntry, new_content: str, new_tags: list[str] | None
) -> dict[str, Any]:
    """Update existing memory with new content (consolidation).

    Takes a MemoryEntry (with path already known) to avoid re-scanning.

    Args:
        entry: MemoryEntry to update
        new_content: New content to replace old content
        new_tags: New tags to merge with existing tags

    Returns:
        dict with keys:
            - display: Pre-formatted string for user
            - path: File path where memory was updated
            - memory_id: ID of updated memory
            - action: "consolidated"
    """
    # Read current frontmatter from the known path
    content = entry.path.read_text(encoding="utf-8")
    existing_fm, _ = parse_frontmatter(content)

    # Merge tags (union)
    existing_tags = existing_fm.get("tags", [])
    merged_tags = list(set(existing_tags + (new_tags or [])))

    # Update frontmatter
    existing_fm["updated"] = datetime.now(timezone.utc).isoformat()
    existing_fm["tags"] = merged_tags
    existing_fm["source"] = _detect_source(merged_tags)
    existing_fm["auto_category"] = _detect_category(merged_tags)

    # Clean up dead metadata from old files
    existing_fm.pop("consolidation_reason", None)

    # Write back to same file (in-place update)
    md_content = (
        f"---\n{yaml.dump(existing_fm, default_flow_style=False)}---\n\n"
        f"{new_content.strip()}\n"
    )
    entry.path.write_text(md_content, encoding="utf-8")

    logger.info(f"Updated memory {entry.id} (consolidation)")

    return {
        "display": (
            f"✓ Updated memory {entry.id} (consolidated duplicate)\n"
            f"Location: {entry.path}"
        ),
        "path": str(entry.path),
        "memory_id": entry.id,
        "action": "consolidated",
    }


# ---------------------------------------------------------------------------
# Decay strategies
# ---------------------------------------------------------------------------


async def _decay_summarize(
    ctx: RunContext[CoDeps],
    memory_dir: Path,
    memories_to_decay: list[MemoryEntry],
    all_memories: list[MemoryEntry],
) -> dict[str, Any]:
    """Decay strategy: Consolidate oldest memories into a simple summary.

    Note: For MVP, uses simple concatenation rather than LLM summarization.

    Args:
        ctx: Agent runtime context
        memory_dir: Path to memories directory
        memories_to_decay: List of oldest memories to decay
        all_memories: Full loaded memory list (used to compute next ID)

    Returns:
        dict with keys:
            - decayed: Number of memories decayed
            - strategy: "summarize"
    """
    if not memories_to_decay:
        return {"decayed": 0, "strategy": "summarize"}

    # Simple concatenation (MVP approach — no LLM call needed)
    summary_lines = [f"Consolidated {len(memories_to_decay)} old memories:", ""]
    for m in memories_to_decay:
        tags_str = f" [tags: {', '.join(m.tags)}]" if m.tags else ""
        summary_lines.append(f"- {m.content}{tags_str}")

    summary_text = "\n".join(summary_lines)

    # Delete original memory files
    deleted_count = 0
    for m in memories_to_decay:
        try:
            m.path.unlink()
            logger.info(f"Deleted memory {m.id} during decay")
            deleted_count += 1
        except Exception as e:
            logger.warning(f"Failed to delete memory {m.id}: {e}")

    # Save summary as new memory (next ID from loaded list)
    max_id = max((m.id for m in all_memories), default=0)
    memory_id = max_id + 1
    slug = _slugify(summary_text[:50])
    filename = f"{memory_id:03d}-{slug}.md"

    frontmatter = {
        "id": memory_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": ["_consolidated", "_auto_decay"],
        "source": "auto_decay",
        "auto_category": None,
    }

    md_content = (
        f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n"
        f"{summary_text.strip()}\n"
    )
    file_path = memory_dir / filename
    file_path.write_text(md_content, encoding="utf-8")

    logger.info(
        f"Saved consolidated memory {memory_id} (summarized {deleted_count} memories)"
    )

    return {"decayed": deleted_count, "strategy": "summarize"}


async def _decay_cut(
    ctx: RunContext[CoDeps],
    memory_dir: Path,
    memories_to_decay: list[MemoryEntry],
) -> dict[str, Any]:
    """Decay strategy: Delete oldest memories permanently.

    Args:
        ctx: Agent runtime context
        memory_dir: Path to memories directory
        memories_to_decay: List of oldest memories to decay

    Returns:
        dict with keys:
            - decayed: Number of memories decayed
            - strategy: "cut"
    """
    if not memories_to_decay:
        return {"decayed": 0, "strategy": "cut"}

    deleted = 0
    for m in memories_to_decay:
        try:
            m.path.unlink()
            logger.info(f"Deleted memory {m.id} during cut decay")
            deleted += 1
        except Exception as e:
            logger.warning(f"Failed to delete memory {m.id}: {e}")

    return {"decayed": deleted, "strategy": "cut"}


async def _decay_memories(
    ctx: RunContext[CoDeps],
    memory_dir: Path,
    memories: list[MemoryEntry],
) -> dict[str, Any]:
    """Trigger memory decay based on configured strategy.

    Args:
        ctx: Agent runtime context
        memory_dir: Path to memories directory
        memories: Already-loaded list of all memories

    Returns:
        dict with keys:
            - decayed: Number of memories decayed
            - strategy: "summarize" | "cut"
    """
    total_count = len(memories)

    # Calculate how many to decay
    decay_count = int(total_count * ctx.deps.memory_decay_percentage)
    if decay_count == 0:
        decay_count = 1  # Always decay at least 1 when triggered

    # Get oldest unprotected memories
    oldest = sorted(
        [m for m in memories if not m.decay_protected],
        key=lambda m: m.created,
    )[:decay_count]

    # Execute decay strategy
    strategy = ctx.deps.memory_decay_strategy
    if strategy == "summarize":
        return await _decay_summarize(ctx, memory_dir, oldest, memories)
    elif strategy == "cut":
        return await _decay_cut(ctx, memory_dir, oldest)
    else:
        logger.warning(
            f"Unknown decay strategy: {strategy}, using summarize"
        )
        return await _decay_summarize(ctx, memory_dir, oldest, memories)


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------


async def save_memory(
    ctx: RunContext[CoDeps],
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Save a memory for cross-session persistence.

    Use this when the user shares important, actionable information that should
    persist across sessions:
    - Preferences: "I prefer X" → tags=["preference", domain]
    - Corrections: "Actually, we use Y" → tags=["correction", domain]
    - Decisions: "We chose Z" → tags=["decision", domain]
    - Context: "Our team has N people" → tags=["context"]
    - Patterns: "We always do X" → tags=["pattern", domain]

    Do NOT save:
    - Speculation or hypotheticals ("Maybe", "I think")
    - Transient conversation details
    - Information already in context files
    - Questions ("Should we?")

    Memory lifecycle (notes with gravity):
    - Checks recent memories for duplicates using string similarity
    - If duplicate found (>85% similar), updates existing memory (consolidation)
    - If unique, appends new memory to sequence
    - When limit reached (200 by default), oldest memories decay automatically

    Creates a markdown file with YAML frontmatter in .co-cli/knowledge/memories/
    Filename format: {id:03d}-{slug}.md where slug is derived from content.

    Args:
        ctx: Agent runtime context
        content: Memory content (markdown, < 500 chars recommended)
        tags: Optional tags for categorization. Use signal type as first tag:
              ["preference", ...], ["correction", ...], ["decision", ...], etc.

    Returns:
        dict with keys:
            - display: Pre-formatted string for user
            - path: File path where memory was saved
            - memory_id: Assigned memory ID
            - action: "saved" or "consolidated" (indicates if dedup occurred)

    Example:
        User: "I prefer pytest over unittest"
        Call: save_memory(ctx, "User prefers pytest over unittest",
                          tags=["preference", "testing", "python"])
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Load all memories once
    memories = _load_all_memories(memory_dir)

    # Step 1: Check for duplicates in recent memories
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=ctx.deps.memory_dedup_window_days
    )
    recent = sorted(
        [m for m in memories if _parse_created(m.created) >= cutoff],
        key=lambda m: m.created,
        reverse=True,
    )[:10]

    is_dup, match, similarity = _check_duplicate(
        content, recent, threshold=ctx.deps.memory_dedup_threshold
    )

    # Step 2: If duplicate found, update existing memory
    if is_dup and match is not None:
        logger.info(
            f"Duplicate detected (similarity: {similarity:.1f}%) "
            f"- updating memory {match.id}"
        )
        result = _update_existing_memory(match, content, tags)
        result["similarity"] = similarity
        return result

    # Step 3: No duplicate — create new memory
    max_id = max((m.id for m in memories), default=0)
    memory_id = max_id + 1
    slug = _slugify(content[:50])
    filename = f"{memory_id:03d}-{slug}.md"

    frontmatter = {
        "id": memory_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": tags or [],
        "source": _detect_source(tags),
        "auto_category": _detect_category(tags),
    }

    md_content = (
        f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n"
        f"{content.strip()}\n"
    )

    file_path = memory_dir / filename
    file_path.write_text(md_content, encoding="utf-8")

    logger.info(f"Saved memory {memory_id} to {file_path}")

    # Step 4: Check size limit and trigger decay if needed
    # Include newly created memory in the list for correct decay computation
    new_entry = MemoryEntry(
        id=memory_id,
        path=file_path,
        content=content.strip(),
        tags=tags or [],
        created=frontmatter["created"],
    )
    all_memories = memories + [new_entry]
    total_count = len(all_memories)

    decay_info = ""
    if total_count > ctx.deps.memory_max_count:
        logger.info(
            f"Memory limit exceeded ({total_count}/{ctx.deps.memory_max_count}) "
            f"- triggering decay"
        )
        decay_result = await _decay_memories(ctx, memory_dir, all_memories)
        decay_info = (
            f"\n♻️ Decayed {decay_result['decayed']} old memories "
            f"({decay_result['strategy']})"
        )

    return {
        "display": (
            f"✓ Saved memory {memory_id}: {filename}\n"
            f"Location: {file_path}{decay_info}"
        ),
        "path": str(file_path),
        "memory_id": memory_id,
        "action": "saved",
    }


async def recall_memory(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search memories using keyword search.

    Use this proactively when:
    - User mentions a preference/decision from past conversations
    - Starting work where prior context would be helpful
    - User explicitly asks about past information

    Searches memory content and tags for matches. Uses case-insensitive
    grep-style matching. Returns most recent matches first.

    Args:
        ctx: Agent runtime context
        query: Search query string (keywords like "python testing" or "database")
        max_results: Maximum number of results to return (default 5)

    Returns:
        dict with keys:
            - display: Pre-formatted markdown string for user
            - count: Number of results found
            - results: List of matching memory dicts with id, content, tags, created

    Example:
        User: "Write tests for the API"
        Call: recall_memory(ctx, "testing python", max_results=3)
        → Finds: "User prefers pytest over unittest"
        → Use this to write pytest tests
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    memories = _load_all_memories(memory_dir)

    # Filter by query (case-insensitive body + tag search)
    query_lower = query.lower()
    matches = [
        m
        for m in memories
        if query_lower in m.content.lower()
        or any(query_lower in t.lower() for t in m.tags)
    ]

    # Sort by recency (created desc)
    matches.sort(key=lambda m: m.created, reverse=True)
    matches = matches[:max_results]

    if not matches:
        return {
            "display": f"No memories found matching '{query}'",
            "count": 0,
            "results": [],
        }

    # Format as markdown list
    lines = [
        f"Found {len(matches)} memor{'y' if len(matches) == 1 else 'ies'} "
        f"matching '{query}':\n"
    ]
    result_dicts: list[dict[str, Any]] = []
    for r in matches:
        lines.append(f"**Memory {r.id}** (created {r.created[:10]})")
        if r.tags:
            lines.append(f"Tags: {', '.join(r.tags)}")
        lines.append(f"{r.content}\n")
        result_dicts.append(
            {
                "id": r.id,
                "path": str(r.path),
                "content": r.content,
                "tags": r.tags,
                "created": r.created,
            }
        )

    return {
        "display": "\n".join(lines),
        "count": len(matches),
        "results": result_dicts,
    }


async def list_memories(
    ctx: RunContext[CoDeps],
) -> dict[str, Any]:
    """List all memories with IDs and metadata.

    Returns all memories sorted by ID with first line as summary.
    Shows consolidation indicators and memory limit status.

    Args:
        ctx: Agent runtime context

    Returns:
        dict with keys:
            - display: Pre-formatted markdown string for user
            - count: Total number of memories
            - limit: Memory limit from settings
            - memories: List of memory summary dicts
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    memories = _load_all_memories(memory_dir)

    if not memories:
        no_dir = not memory_dir.exists()
        msg = "No memories saved yet." if no_dir else "No memories found."
        return {
            "display": msg,
            "count": 0,
            "limit": ctx.deps.memory_max_count,
            "memories": [],
        }

    # Sort by ID
    memories.sort(key=lambda m: m.id)

    # Build summary dicts
    memory_dicts: list[dict[str, Any]] = []
    for m in memories:
        body_lines = m.content.split("\n")
        summary = body_lines[0] if body_lines else "(empty)"
        if len(summary) > 80:
            summary = summary[:77] + "..."

        memory_dicts.append(
            {
                "id": m.id,
                "created": m.created,
                "updated": m.updated,
                "tags": m.tags,
                "auto_category": _detect_category(m.tags),
                "summary": summary,
                "decay_protected": m.decay_protected,
            }
        )

    # Format as markdown list with lifecycle indicators
    lines = [f"Total memories: {len(memories)}/{ctx.deps.memory_max_count}\n"]

    for md in memory_dicts:
        # Format dates
        created_date = md["created"][:10]
        if md.get("updated"):
            updated_date = md["updated"][:10]
            date_str = f"{created_date} → {updated_date}"
        else:
            date_str = created_date

        # Format category
        category_str = (
            f" [{md['auto_category']}]" if md.get("auto_category") else ""
        )

        # Format protection indicator
        protected_str = " 🔒" if md.get("decay_protected") else ""

        lines.append(
            f"**{md['id']:03d}** ({date_str}){category_str}{protected_str} "
            f": {md['summary']}"
        )

    return {
        "display": "\n".join(lines),
        "count": len(memories),
        "limit": ctx.deps.memory_max_count,
        "memories": memory_dicts,
    }
