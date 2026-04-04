"""Shared primitives for tool error handling."""

from typing import TYPE_CHECKING

from pydantic_ai import ModelRetry

from co_cli.tools.tool_output import ToolResult, tool_output

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from co_cli.deps import CoDeps


def tool_error(
    message: str,
    *,
    ctx: "RunContext[CoDeps] | None" = None,
) -> ToolResult:
    """Return a ToolResult for terminal (non-retryable) tool failures.

    Unlike ModelRetry, this stops the retry loop immediately — the model
    sees the error in the tool result and can pick a different tool.
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
    ctx: "RunContext[CoDeps] | None" = None,
) -> ToolResult:
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
