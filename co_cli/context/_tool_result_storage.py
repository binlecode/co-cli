"""Persistence engine for oversized tool results.

When a tool result exceeds TOOL_RESULT_MAX_SIZE, it is persisted to disk
and replaced with a preview + file-path reference. The model never sees
the full content — not even within the same turn.

Content-addressed file naming via SHA-256 hash prefix ensures idempotency:
the same content always maps to the same file.
"""

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

TOOL_RESULT_MAX_SIZE = 50_000
TOOL_RESULT_PREVIEW_SIZE = 2_000
PERSISTED_OUTPUT_TAG = "<persisted-output>"


def persist_if_oversized(
    content: str,
    tool_results_dir: Path,
    tool_name: str,
) -> str:
    """Persist content to disk if oversized, returning a preview placeholder.

    If content length <= TOOL_RESULT_MAX_SIZE, returns content unchanged.
    Otherwise, writes content to a content-addressed file and returns an XML
    placeholder with tool name, file path, and a 2KB preview.

    Args:
        content: The full tool result text.
        tool_results_dir: Directory for persisted tool result files.
        tool_name: Name of the tool that produced the result.

    Returns:
        The original content if under threshold, or a preview placeholder.
    """
    if len(content) <= TOOL_RESULT_MAX_SIZE:
        return content

    try:
        hash_prefix = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        tool_results_dir.mkdir(parents=True, exist_ok=True)
        file_path = tool_results_dir / f"{hash_prefix}.txt"

        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")

        preview = content[:TOOL_RESULT_PREVIEW_SIZE]

        return (
            f"{PERSISTED_OUTPUT_TAG}\n"
            f"tool: {tool_name}\n"
            f"file: {file_path}\n"
            f"size: {len(content)} chars\n"
            f"preview:\n{preview}\n"
            f"</persisted-output>"
        )
    except OSError:
        log.warning(
            "Failed to persist oversized tool result for %s, returning full content",
            tool_name,
        )
        return content


def check_tool_results_size(
    tool_results_dir: Path,
    warn_threshold_mb: int = 100,
) -> str | None:
    """Return a warning string if tool-results directory exceeds the threshold.

    Args:
        tool_results_dir: Path to the tool results directory.
        warn_threshold_mb: Size threshold in megabytes.

    Returns:
        Warning string if directory exceeds threshold, None otherwise.
    """
    if not tool_results_dir.is_dir():
        return None

    total_bytes = sum(f.stat().st_size for f in tool_results_dir.iterdir() if f.is_file())
    total_mb = total_bytes / (1024 * 1024)

    if total_mb > warn_threshold_mb:
        return (
            f"Tool results directory {tool_results_dir} is {total_mb:.0f} MB "
            f"(threshold: {warn_threshold_mb} MB). Consider cleaning up old files."
        )
    return None
