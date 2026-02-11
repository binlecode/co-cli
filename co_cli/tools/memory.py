"""Memory management tools for persistent knowledge.

This module provides tools for saving, recalling, and listing memories in the
internal knowledge system. Memories are stored as markdown files with YAML
frontmatter in .co-cli/knowledge/memories/.

Retrieval uses grep-based search for MVP (<200 memories). Future phases will
add SQLite FTS5 and vector search as corpus grows.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai import RunContext

from co_cli._frontmatter import parse_frontmatter, validate_memory_frontmatter
from co_cli.deps import CoDeps

logger = logging.getLogger(__name__)


def _next_memory_id() -> int:
    """Get next available memory ID by scanning existing files.

    Scans .co-cli/knowledge/memories/ for existing memory files,
    parses their frontmatter, and returns max(id) + 1.

    Returns:
        Next available memory ID (starts at 1 if no memories exist)
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    if not memory_dir.exists():
        return 1

    max_id = 0
    for path in memory_dir.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
            frontmatter, _ = parse_frontmatter(content)
            if frontmatter and "id" in frontmatter:
                memory_id = frontmatter["id"]
                if isinstance(memory_id, int) and memory_id > max_id:
                    max_id = memory_id
        except Exception as e:
            logger.warning(f"Failed to parse {path} for ID: {e}")

    return max_id + 1


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug (max 50 chars).

    Args:
        text: Text to slugify

    Returns:
        Slugified text (lowercase, hyphens, no special chars)
    """
    # Convert to lowercase, replace non-alphanumeric with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    # Strip leading/trailing hyphens, limit to 50 chars
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


def _search_memories(
    query: str, memory_dir: Path, max_results: int = 5
) -> list[dict[str, Any]]:
    """Search memories using grep + frontmatter scan.

    Searches memory content and tags for case-insensitive matches.

    Args:
        query: Search query string
        memory_dir: Path to memories directory
        max_results: Maximum number of results to return

    Returns:
        List of dicts with keys: id, path, content, tags, created
        Sorted by recency (created desc)
    """
    if not memory_dir.exists():
        return []

    results = []
    query_lower = query.lower()

    for path in memory_dir.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)

            # Validate frontmatter
            try:
                validate_memory_frontmatter(frontmatter)
            except ValueError:
                continue  # Skip invalid memories

            # Search in body content
            body_match = query_lower in body.lower()

            # Search in tags
            tags = frontmatter.get("tags", [])
            tag_match = any(query_lower in tag.lower() for tag in tags)

            if body_match or tag_match:
                results.append(
                    {
                        "id": frontmatter["id"],
                        "path": str(path),
                        "content": body.strip(),
                        "tags": tags,
                        "created": frontmatter["created"],
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to search {path}: {e}")

    # Sort by recency (created desc)
    results.sort(key=lambda r: r["created"], reverse=True)
    return results[:max_results]


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

    Example:
        User: "I prefer pytest over unittest"
        Call: save_memory(ctx, "User prefers pytest over unittest",
                          tags=["preference", "testing", "python"])
    """
    memory_id = _next_memory_id()
    slug = _slugify(content[:50])
    filename = f"{memory_id:03d}-{slug}.md"

    frontmatter = {
        "id": memory_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": tags or [],
        "source": _detect_source(tags),
        "auto_category": _detect_category(tags),
    }

    # Format as markdown with frontmatter
    md_content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{content.strip()}\n"

    # Write to file
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True, exist_ok=True)
    file_path = memory_dir / filename
    file_path.write_text(md_content, encoding="utf-8")

    logger.info(f"Saved memory {memory_id} to {file_path}")

    return {
        "display": f"✓ Saved memory {memory_id}: {filename}\nLocation: {file_path}",
        "path": str(file_path),
        "memory_id": memory_id,
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
    results = _search_memories(query, memory_dir, max_results)

    if not results:
        return {
            "display": f"No memories found matching '{query}'",
            "count": 0,
            "results": [],
        }

    # Format as markdown list
    lines = [f"Found {len(results)} memor{'y' if len(results) == 1 else 'ies'} matching '{query}':\n"]
    for r in results:
        lines.append(f"**Memory {r['id']}** (created {r['created'][:10]})")
        if r["tags"]:
            lines.append(f"Tags: {', '.join(r['tags'])}")
        lines.append(f"{r['content']}\n")

    return {
        "display": "\n".join(lines),
        "count": len(results),
        "results": results,
    }


async def list_memories(
    ctx: RunContext[CoDeps],
) -> dict[str, Any]:
    """List all memories with IDs and metadata.

    Returns all memories sorted by ID with first line as summary.

    Args:
        ctx: Agent runtime context

    Returns:
        dict with keys:
            - display: Pre-formatted markdown string for user
            - count: Total number of memories
            - memories: List of memory summary dicts
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"

    if not memory_dir.exists():
        return {"display": "No memories saved yet.", "count": 0, "memories": []}

    memories = []
    for path in memory_dir.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)

            # Validate frontmatter
            try:
                validate_memory_frontmatter(frontmatter)
            except ValueError:
                continue  # Skip invalid memories

            # Extract first line as summary
            body_lines = body.strip().split("\n")
            summary = body_lines[0] if body_lines else "(empty)"
            if len(summary) > 80:
                summary = summary[:77] + "..."

            memories.append(
                {
                    "id": frontmatter["id"],
                    "created": frontmatter["created"],
                    "tags": frontmatter.get("tags", []),
                    "auto_category": frontmatter.get("auto_category"),
                    "summary": summary,
                }
            )
        except Exception as e:
            logger.warning(f"Failed to list {path}: {e}")

    if not memories:
        return {"display": "No memories found.", "count": 0, "memories": []}

    # Sort by ID
    memories.sort(key=lambda m: m["id"])

    # Format as markdown list
    lines = [f"Total memories: {len(memories)}\n"]
    for m in memories:
        category_str = f" [{m['auto_category']}]" if m.get("auto_category") else ""
        lines.append(f"**{m['id']:03d}** ({m['created'][:10]}){category_str} : {m['summary']}")

    return {
        "display": "\n".join(lines),
        "count": len(memories),
        "memories": memories,
    }
