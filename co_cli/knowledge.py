"""Memory loading from markdown context files.

This module loads persistent memory from markdown files:
- Global context: ~/.config/co-cli/knowledge/context.md
- Project context: .co-cli/knowledge/context.md

Memory is injected into the system prompt for every agent session.
"""

import logging
import sys
from pathlib import Path

from co_cli._frontmatter import parse_frontmatter, strip_frontmatter, validate_context_frontmatter

logger = logging.getLogger(__name__)

# Size budgets (bytes)
SIZE_TARGET = 10 * 1024  # 10 KiB soft limit - warn user
SIZE_LIMIT = 20 * 1024  # 20 KiB hard limit - truncate


def load_memory() -> str | None:
    """Load memory from markdown context files.

    Loads memory from:
      - Global: ~/.config/co-cli/knowledge/context.md (3 KiB budget)
      - Project: .co-cli/knowledge/context.md (7 KiB budget, overrides global)

    Returns markdown-formatted memory for prompt injection.
    Validates frontmatter and enforces size limits.

    Returns:
        Markdown string with global/project sections, or None if no memory exists.

    Side effects:
        - Logs info when loading files
        - Warns to stderr if total size 10-20 KiB
        - Errors to stderr and truncates if size >20 KiB
    """
    global_path = Path.home() / ".config/co-cli/knowledge/context.md"
    project_path = Path.cwd() / ".co-cli/knowledge/context.md"

    sections: list[tuple[str, str]] = []

    # Load global context
    if global_path.exists():
        try:
            content = global_path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)

            # Validate frontmatter if present
            if frontmatter:
                try:
                    validate_context_frontmatter(frontmatter)
                except ValueError as e:
                    logger.warning(f"Invalid frontmatter in {global_path}: {e}")
                    # Continue with body only

            body = body.strip()
            if body:
                sections.append(("Global Context", body))
                logger.info(f"Loaded global memory from {global_path}")
        except Exception as e:
            logger.warning(f"Failed to load global memory from {global_path}: {e}")

    # Load project context (overrides global if same keys present)
    if project_path.exists():
        try:
            content = project_path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)

            # Validate frontmatter if present
            if frontmatter:
                try:
                    validate_context_frontmatter(frontmatter)
                except ValueError as e:
                    logger.warning(f"Invalid frontmatter in {project_path}: {e}")
                    # Continue with body only

            body = body.strip()
            if body:
                sections.append(("Project Context", body))
                logger.info(f"Loaded project memory from {project_path}")
        except Exception as e:
            logger.warning(f"Failed to load project memory from {project_path}: {e}")

    if not sections:
        return None

    # Combine sections
    section_parts = []
    for title, body in sections:
        section_parts.append(f"### {title}\n\n{body}")

    combined = "\n\n".join(section_parts)
    memory = f"## Background Reference (not current conversation)\n\n{combined}"

    # Validate size
    size = len(memory.encode("utf-8"))

    if size > SIZE_LIMIT:
        # Hard limit exceeded - truncate
        print(
            f"ERROR: Memory size {size} bytes exceeds hard limit {SIZE_LIMIT} bytes. Truncating to limit.",
            file=sys.stderr,
        )
        # Truncate at byte level with UTF-8 error handling
        truncated_bytes = memory.encode("utf-8")[:SIZE_LIMIT]
        memory = truncated_bytes.decode("utf-8", errors="ignore")
    elif size > SIZE_TARGET:
        # Soft limit exceeded - warn
        print(
            f"WARNING: Memory size {size} bytes exceeds target {SIZE_TARGET} bytes. "
            f"Consider trimming context files to improve performance.",
            file=sys.stderr,
        )

    return memory
