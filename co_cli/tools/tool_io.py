"""Tool I/O — result construction, error handling, and oversized result persistence.

tool_output() returns ToolReturn(return_value=display, metadata=metadata_dict).
pydantic-ai places the display string into ToolReturnPart.content (model sees plain
text) and metadata into ToolReturnPart.metadata (app-side, not sent to LLM).

Usage:
    from co_cli.tools.tool_io import tool_output

    return tool_output("formatted display text", ctx=ctx, count=3)

For call sites without RunContext (helper functions, lifecycle modules):
    from co_cli.tools.tool_io import tool_output_raw

    return tool_output_raw("formatted display text", action="saved")
"""

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolReturn

if TYPE_CHECKING:
    from pydantic_ai import RunContext

    from co_cli.deps import CoDeps


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------

TOOL_RESULT_MAX_SIZE = 50_000
TOOL_RESULT_PREVIEW_SIZE = 2_000
PERSISTED_OUTPUT_TAG = "<persisted-output>"


def persist_if_oversized(
    content: str,
    tool_results_dir: Path,
    tool_name: str,
    *,
    max_size: int = TOOL_RESULT_MAX_SIZE,
) -> str:
    """Persist content to disk if oversized, returning a preview placeholder.

    If content length <= max_size, returns content unchanged.
    Otherwise, writes content to a content-addressed file and returns an XML
    placeholder with tool name, file path, and a 2KB preview.

    Args:
        content: The full tool result text.
        tool_results_dir: Directory for persisted tool result files.
        tool_name: Name of the tool that produced the result.
        max_size: Per-tool result size threshold (default: TOOL_RESULT_MAX_SIZE).

    Returns:
        The original content if under threshold, or a preview placeholder.
    """
    if len(content) <= max_size:
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
            f"To read the full output, call read_file with the path above and use "
            f"start_line/end_line to page through it in chunks.\n"
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


# ---------------------------------------------------------------------------
# Result construction
# ---------------------------------------------------------------------------


def tool_output(
    display: str,
    *,
    ctx: "RunContext[CoDeps]",
    **metadata: Any,
) -> ToolReturn:
    """Construct a ToolReturn with display as return_value and extras as metadata."""
    tool_name = ctx.tool_name or ""
    info = ctx.deps.tool_index.get(tool_name)
    threshold = info.max_result_size if info else TOOL_RESULT_MAX_SIZE
    if len(display) > threshold:
        display = persist_if_oversized(
            display,
            ctx.deps.tool_results_dir,
            tool_name,
            max_size=threshold,
        )
    return ToolReturn(return_value=display, metadata=metadata or None)


def tool_output_raw(
    display: str,
    **metadata: Any,
) -> ToolReturn:
    """Construct a ToolReturn without RunContext — no size checking.

    Use only in helper functions that lack RunContext (e.g. memory lifecycle,
    memory save). Tool functions with ctx should always use tool_output().
    """
    return ToolReturn(return_value=display, metadata=metadata or None)


# Shared type alias for Frontend.on_tool_complete, _run_stream_segment dispatch,
# and TerminalFrontend._render_tool_panel — one edit point if a new result type is added.
ToolResultPayload = str | ToolReturn | None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def tool_error(
    message: str,
    *,
    ctx: "RunContext[CoDeps]",
) -> ToolReturn:
    """Return a ToolReturn for terminal (non-retryable) tool failures.

    Unlike ModelRetry, this stops the retry loop immediately — the model
    sees the error in the tool result and can pick a different tool.

    Tool functions always have RunContext; use this helper. For ctx-less
    helpers (e.g. _http_get_with_retries), call tool_output_raw(..., error=True)
    directly.
    """
    return tool_output(message, ctx=ctx, error=True)


def http_status_code(e: Exception) -> int | None:
    """Extract HTTP status code from common API exception shapes."""
    status = getattr(e, "status_code", None)
    if status is not None:
        return int(status)

    resp = getattr(e, "resp", None)
    if resp is None:
        return None

    raw_status = getattr(resp, "status", None)
    if raw_status is None:
        return None

    try:
        return int(raw_status)
    except (TypeError, ValueError):
        return None


def handle_google_api_error(
    label: str,
    e: Exception,
    *,
    ctx: "RunContext[CoDeps]",
) -> ToolReturn:
    """Route Google API errors to tool_error or ModelRetry.

    401 → terminal (auth failure, user must fix credentials)
    403/404/429/5xx → ModelRetry (transient or permission issue worth retrying)
    """
    status = http_status_code(e)
    if status == 401:
        return tool_error(f"{label}: authentication error (401). Check credentials.", ctx=ctx)
    if status == 403:
        raise ModelRetry(f"{label}: access forbidden (403). Check API enablement and permissions.")
    if status == 404:
        raise ModelRetry(f"{label}: resource not found (404). Verify the ID and retry.")
    if status == 429:
        raise ModelRetry(f"{label}: rate limited (429). Wait a moment and retry.")
    if status and status >= 500:
        raise ModelRetry(f"{label}: server error ({status}). Retry shortly.")
    raise ModelRetry(f"{label}: API error ({e}). Check credentials, API enablement, and quota.")
