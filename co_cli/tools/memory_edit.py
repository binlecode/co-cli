"""Memory edit tools — surgical update and append for existing memory files.

Write path for targeted in-place edits. Does not create new memory files;
use save_memory (tools/memory_write.py) for that.
"""

import hashlib
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.knowledge._frontmatter import parse_frontmatter, render_memory_file
from co_cli.tools.resource_lock import ResourceBusyError
from co_cli.tools.tool_errors import tool_error
from co_cli.tools.tool_output import tool_output

_TRACER = otel_trace.get_tracer("co.memory")

# Matches line-number prefixes injected by the Read tool (e.g. "1→ " or "Line 1: ")
_LINE_PREFIX_RE = re.compile(r"(^|\n)\d+\u2192 ", re.MULTILINE)
_LINE_NUM_RE = re.compile(r"\nLine \d+: ")


def _find_by_slug(memory_dir: Path, slug: str) -> Path | None:
    """Return the memory file whose stem matches slug, or None."""
    return next((p for p in memory_dir.glob("*.md") if p.stem == slug), None)


async def update_memory(
    ctx: RunContext[CoDeps],
    slug: str,
    old_content: str,
    new_content: str,
) -> ToolReturn:
    """Surgically replace a specific passage in a memory file without rewriting
    the entire body.  Safer than save_memory for targeted edits — no dedup
    path, no full-body replacement.

    *slug* is the full file stem, e.g. ``"001-dont-use-trailing-comments"``.
    Use list_memories to find it.

    Guards applied before any I/O:
    - Rejects old_content / new_content that contain Read-tool line-number
      prefixes (``1→ `` or ``Line N: ``).
    - old_content must appear exactly once in the body (case-sensitive).

    Returns a dict with:
    - display: confirmation + updated body text
    - slug: the memory slug that was edited

    Args:
        slug: Full file stem of the target memory (e.g. "003-user-prefers-pytest").
        old_content: Exact passage to replace (must appear exactly once).
        new_content: Replacement text.
    """
    knowledge_dir = ctx.deps.memory_dir
    match = _find_by_slug(knowledge_dir, slug)
    if match is None:
        raise FileNotFoundError(f"Memory '{slug}' not found")

    # Guard: reject Read-tool line-number artifacts
    for s, name in ((old_content, "old_content"), (new_content, "new_content")):
        if _LINE_PREFIX_RE.search(s) or _LINE_NUM_RE.search(s):
            raise ValueError(
                f"{name} contains line-number prefixes (e.g. '1\u2192 ' or 'Line N: '). "
                "Strip them before calling update_memory."
            )

    try:
        async with ctx.deps.resource_locks.try_acquire(slug):
            raw = match.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)

            # Tab normalization — treat tabs and equivalent spaces as equivalent
            body_text = body.expandtabs()
            old_norm = old_content.expandtabs()
            new_norm = new_content.expandtabs()

            count = body_text.count(old_norm)
            if count == 0:
                raise ValueError(
                    f"old_content not found in memory '{slug}'. "
                    "Check for exact match (case-sensitive, whitespace-sensitive)."
                )
            if count > 1:
                # Find line numbers of each occurrence for a useful error message
                positions: list[int] = []
                pos = 0
                while True:
                    idx = body_text.find(old_norm, pos)
                    if idx == -1:
                        break
                    line_num = body_text[:idx].count("\n") + 1
                    positions.append(line_num)
                    pos = idx + 1
                raise ValueError(
                    f"old_content appears {count} times in '{slug}' "
                    f"(body lines ~{positions}). Provide more context to make it unique."
                )

            with _TRACER.start_as_current_span("co.memory.update") as span:
                span.set_attribute("memory.slug", slug)
                span.set_attribute("memory.action", "update")

                updated_body = body_text.replace(old_norm, new_norm, 1)
                fm["updated"] = datetime.now(UTC).isoformat()
                md_content = render_memory_file(fm, updated_body)
                with tempfile.NamedTemporaryFile(
                    "w", dir=match.parent, suffix=".tmp", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(md_content)
                os.replace(tmp.name, match)

                if ctx.deps.knowledge_store is not None:
                    content_hash = hashlib.sha256(md_content.encode()).hexdigest()
                    ctx.deps.knowledge_store.index(
                        source="memory",
                        kind="memory",
                        path=str(match),
                        title=fm.get("name") or slug,
                        content=updated_body.strip(),
                        mtime=match.stat().st_mtime,
                        hash=content_hash,
                        tags=" ".join(fm.get("tags", [])) or None,
                        created=fm.get("created"),
                        type=fm.get("type"),
                        description=fm.get("description"),
                    )

            return tool_output(
                f"Updated memory '{slug}'.\n{updated_body.strip()}",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Memory '{slug}' is being modified by another tool call — retry next turn"
        )


async def append_memory(
    ctx: RunContext[CoDeps],
    slug: str,
    content: str,
) -> ToolReturn:
    """Append content to the end of an existing memory file.

    Use when new information extends a memory rather than replacing it.
    Safer than update_memory when you don't have an exact passage to match.

    *slug* is the full file stem, e.g. ``"001-dont-use-trailing-comments"``.
    Use list_memories to find it.

    Returns a dict with:
    - display: confirmation message
    - slug: the memory slug that was appended to

    Args:
        slug: Full file stem of the target memory (e.g. "003-user-prefers-pytest").
        content: Text to append (added on a new line at the end of the body).
    """
    knowledge_dir = ctx.deps.memory_dir
    match = _find_by_slug(knowledge_dir, slug)
    if match is None:
        raise FileNotFoundError(f"Memory '{slug}' not found")

    try:
        async with ctx.deps.resource_locks.try_acquire(slug):
            raw = match.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)

            with _TRACER.start_as_current_span("co.memory.append") as span:
                span.set_attribute("memory.slug", slug)
                span.set_attribute("memory.action", "append")

                updated_body = body.rstrip() + "\n" + content
                fm["updated"] = datetime.now(UTC).isoformat()
                md_content = render_memory_file(fm, updated_body)
                with tempfile.NamedTemporaryFile(
                    "w", dir=match.parent, suffix=".tmp", delete=False, encoding="utf-8"
                ) as tmp:
                    tmp.write(md_content)
                os.replace(tmp.name, match)

                if ctx.deps.knowledge_store is not None:
                    content_hash = hashlib.sha256(md_content.encode()).hexdigest()
                    ctx.deps.knowledge_store.index(
                        source="memory",
                        kind="memory",
                        path=str(match),
                        title=fm.get("name") or slug,
                        content=updated_body.strip(),
                        mtime=match.stat().st_mtime,
                        hash=content_hash,
                        tags=" ".join(fm.get("tags", [])) or None,
                        created=fm.get("created"),
                        type=fm.get("type"),
                        description=fm.get("description"),
                    )

            return tool_output(
                f"Appended to '{slug}'.",
                ctx=ctx,
                slug=slug,
            )
    except ResourceBusyError:
        return tool_error(
            f"Memory '{slug}' is being modified by another tool call — retry next turn"
        )
