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
import os
import re
import uuid
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

TOOL_RESULT_PREVIEW_SIZE = 2_000
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"


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


def persist_if_oversized(
    content: str,
    tool_results_dir: Path,
    tool_name: str,
    *,
    max_size: int | float,
) -> str:
    """Persist content to disk if oversized, returning a preview placeholder.

    If content length <= max_size, returns content unchanged.
    Otherwise, writes content to a content-addressed file and returns an XML
    placeholder with tool name, file path, human-readable size, and a preview.

    Args:
        content: The full tool result text.
        tool_results_dir: Directory for persisted tool result files.
        tool_name: Name of the tool that produced the result.
        max_size: Per-tool result size threshold. Pass 0 to force spill.

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
            tmp_path = file_path.with_suffix(f".txt.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, file_path)

        preview, has_more = _generate_preview(content, TOOL_RESULT_PREVIEW_SIZE)
        elision = "\n..." if has_more else ""

        size_chars = len(content)
        if size_chars >= 1024 * 1024:
            size_human = f"{size_chars / (1024 * 1024):.1f} MB"
        else:
            size_human = f"{size_chars / 1024:.1f} KB"

        return (
            f"{PERSISTED_OUTPUT_TAG}\n"
            f"tool: {tool_name}\n"
            f"file: {file_path}\n"
            f"size: {size_chars:,} chars ({size_human})\n"
            f"To read the full output, call read_file with the path above and use "
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


_TMP_RESULT_NAME_RE = re.compile(r"^[0-9a-f]+\.txt\.tmp\.(\d+)\.[0-9a-f]+$")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sweep_tool_result_orphans(tool_results_dir: Path) -> int:
    """Unlink stale `*.tmp.*` sidecars left by crashed persist_if_oversized writes.

    Matches files with the shape produced at line 91 above: `<hash>.txt.tmp.<pid>.<uuid>`.
    Preserves sidecars whose embedded PID is still a live process — another `co`
    process's in-flight write is safe. Never raises; sweep failures must not block startup.

    Returns the count of files removed.
    """
    if not tool_results_dir.is_dir():
        return 0

    try:
        entries = list(tool_results_dir.iterdir())
    except OSError:
        return 0

    removed = 0
    for entry in entries:
        match = _TMP_RESULT_NAME_RE.match(entry.name)
        if match is None:
            continue
        try:
            pid = int(match.group(1))
        except ValueError:
            continue
        if _pid_alive(pid):
            continue
        try:
            entry.unlink(missing_ok=True)
        except OSError:
            continue
        removed += 1
    return removed


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
    threshold = (
        info.max_result_size
        if info and info.max_result_size is not None
        else ctx.deps.config.tools.result_persist_chars
    )
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
