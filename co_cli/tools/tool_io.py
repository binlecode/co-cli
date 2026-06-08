"""Tool I/O — result construction, error handling, and oversized result persistence.

tool_output() returns ToolReturn(return_value=display, metadata=metadata_dict).
pydantic-ai places the display string into ToolReturnPart.content (model sees plain
text) and metadata into ToolReturnPart.metadata (app-side, not sent to LLM).

All tool results are constructed at the ctx-bearing entrypoint via
tool_output() / tool_error(); both route through spill_with_span so every
result respects the per-tool spill threshold. Impl helpers without ctx
return raw data or error strings — never a ToolReturn — and the entrypoint
wraps the error via tool_error().

Usage:
    from co_cli.tools.tool_io import tool_output

    return tool_output("formatted display text", ctx=ctx, count=3)
"""

import hashlib
import logging
import math
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolReturn

from co_cli.fileio.atomic import atomic_write_text
from co_cli.observability.tracing import current_span

if TYPE_CHECKING:
    from pydantic_ai import RunContext

    from co_cli.deps import CoDeps


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------

TOOL_RESULT_PREVIEW_CHARS = 1_500
SPILL_THRESHOLD_CHARS = 4_000
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"

# Pagination cap for the read/view tools that page over a source (file_read,
# session_view). One read returns <= READ_MAX_LINES lines/turns; the continuation
# hint pages forward. 500 of typical source (~52 chars/line incl. line-number
# prefix) is ~26k chars — inline and (via the L2 tail-protection sibling) visible.
READ_MAX_LINES = 500


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
    If content length <= TOOL_RESULT_PREVIEW_CHARS, returns content unchanged regardless.
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
    if len(content) <= TOOL_RESULT_PREVIEW_CHARS:
        return content

    try:
        hash_prefix = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
        file_path = tool_results_dir / f"{hash_prefix}.txt"

        if not file_path.exists():
            atomic_write_text(file_path, content, errors="replace")

        preview, has_more = _generate_preview(content, TOOL_RESULT_PREVIEW_CHARS)
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
    """Unlink stale `*.tmp.*` sidecars left by crashed spill_if_oversized writes.

    Matches files with the shape produced above: `<hash>.txt.tmp.<pid>.<uuid>`.
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
    info = ctx.deps.tool_catalog.get(tool_name)
    threshold: int | float = (
        info.spill_threshold_chars
        if info and info.spill_threshold_chars is not None
        else SPILL_THRESHOLD_CHARS
    )
    display = spill_with_span(
        display,
        tool_name=tool_name,
        tool_results_dir=ctx.deps.tool_results_dir,
        threshold_chars=threshold,
    )
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

    Tool functions always have RunContext; use this helper. Impl helpers
    without ctx (e.g. _http_get_with_retries) return an error string; the
    ctx-bearing entrypoint wraps it via tool_error so every result spills.
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

    RefreshError → terminal (credential invalid or missing required scopes — no
        retry can fix it; the user must re-authorize)
    401 → terminal (auth failure, user must fix credentials)
    403/404/429/5xx → ModelRetry (transient or permission issue worth retrying)
    """
    from google.auth.exceptions import RefreshError

    # A token refresh failure (missing scope, revoked/expired refresh token) is a
    # permanent config error: it carries no HTTP status and renders as a stringified
    # tuple, so type is the authoritative signal. Classify it terminal before the
    # status checks so it never falls through to the retryable catch-all.
    if isinstance(e, RefreshError):
        return tool_error(
            f"{label}: the Google credential is invalid or missing required scopes. "
            "Re-authorize by running `co google auth` to grant: gmail.readonly, "
            "gmail.compose, drive.readonly, calendar.readonly.",
            ctx=ctx,
        )
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
