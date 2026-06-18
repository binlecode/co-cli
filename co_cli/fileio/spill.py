"""Oversized-content spill: persist large text to disk, return a preview placeholder.

Foundational spill primitive shared by the tool-result emit path
(``tools/tool_io.py``) and the compaction force-spill hook
(``context/history_processors.py``). It sits below both consumers and imports only
``config`` + ``fileio`` + ``observability`` — a downward dependency for either caller.
"""

import hashlib
import logging
import math
from pathlib import Path
from typing import Any

from co_cli.config.tuning import (
    PERSISTED_OUTPUT_CLOSING_TAG,
    PERSISTED_OUTPUT_TAG,
    SPILL_PREVIEW_CHARS,
    SPILL_THRESHOLD_CHARS,
)
from co_cli.fileio.atomic import atomic_write_text
from co_cli.observability.tracing import current_span

log = logging.getLogger(__name__)


def _generate_preview(content: str, max_chars: int) -> tuple[str, bool]:
    """Truncate at the last newline within max_chars when it lies past halfway; else hard-cut.

    Returns (preview, has_more). has_more is True iff content was longer than max_chars.
    """
    if len(content) <= max_chars:
        return content, False
    truncated = content[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[: last_nl + 1]
    return truncated, True


def spill_if_oversized(
    content: str,
    tool_results_dir: Path,
    tool_name: str,
    *,
    force: bool = False,
) -> str:
    """Persist content to disk if oversized, returning a preview placeholder.

    If not forced and content length <= SPILL_THRESHOLD_CHARS, returns content unchanged.
    If content length <= SPILL_PREVIEW_CHARS, returns content unchanged regardless.
    Otherwise, writes content to a content-addressed file and returns an XML
    placeholder with tool name, file path, human-readable size, and a preview.

    Args:
        content: The full tool result text.
        tool_results_dir: Directory for persisted tool result files.
        tool_name: Name of the tool that produced the result.
        force: When True, bypasses the SPILL_THRESHOLD_CHARS check (used by
            the L2 round-budget hook for aggregate budget enforcement).

    Returns:
        The original content if under threshold, or a preview placeholder.
    """
    if not force and len(content) <= SPILL_THRESHOLD_CHARS:
        return content
    if len(content) <= SPILL_PREVIEW_CHARS:
        return content

    try:
        hash_prefix = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
        file_path = tool_results_dir / f"{hash_prefix}.txt"

        if not file_path.exists():
            atomic_write_text(file_path, content, errors="replace")

        preview, has_more = _generate_preview(content, SPILL_PREVIEW_CHARS)
        elision = "\n..." if has_more else ""

        size_chars = len(content)
        if size_chars >= 1024 * 1024:
            size_human = f"{size_chars / (1024 * 1024):.1f} MB"
        else:
            size_human = f"{size_chars / 1024:.1f} KB"

        return (
            f"{PERSISTED_OUTPUT_TAG}\n"
            f"This tool result was too large ({size_chars:,} chars, {size_human}).\n"
            f"tool: {tool_name}\n"
            f"file: {file_path}\n"
            f"To read the full output, call file_read with the path above and use "
            f"start_line/end_line to page through it in chunks.\n"
            f"preview:\n{preview}{elision}\n"
            f"{PERSISTED_OUTPUT_CLOSING_TAG}"
        )
    except OSError:
        log.warning(
            "Failed to persist oversized tool result for %s, returning full content",
            tool_name,
        )
        return content


def spill_with_span(
    content: str,
    *,
    tool_name: str,
    tool_results_dir: Path,
    threshold_chars: int | float,
    forced: bool = False,
) -> str:
    """Spill content to disk if oversized, always emitting a tracing span."""
    span_threshold = SPILL_THRESHOLD_CHARS if math.isinf(threshold_chars) else int(threshold_chars)
    content_chars = len(content)
    spill_fired = False
    if content_chars > threshold_chars:
        new_content = spill_if_oversized(content, tool_results_dir, tool_name, force=forced)
        spill_fired = new_content != content
        content = new_content
    event_attrs: dict[str, Any] = {
        "tool.name": tool_name,
        "spill.threshold_chars": span_threshold,
        "spill.content_chars": content_chars,
        "spill.fired": spill_fired,
        "spill.forced": forced,
    }
    if spill_fired:
        event_attrs["spill.savings_chars"] = content_chars - len(content)
    current_span().add_event("tool_budget.spill_tool_result", event_attrs)
    return content
