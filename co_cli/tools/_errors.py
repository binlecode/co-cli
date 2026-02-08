"""Shared error constants and helpers for tool error normalization.

ModelRetry messages follow the format: "{Tool}: {problem}. {Action hint}."
Terminal errors (config/auth) return a dict instead of raising ModelRetry,
so the model sees the error as a tool result and can route to alternatives.
"""

import enum
from typing import Any

from pydantic_ai import ModelRetry


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


class ToolErrorKind(enum.Enum):
    TRANSIENT = "transient"   # rate limit, 5xx, network → raises ModelRetry
    TERMINAL  = "terminal"    # auth failure, not configured → returns dict
    MISUSE    = "misuse"      # bad ID, invalid args → raises ModelRetry with hint


def handle_tool_error(kind: ToolErrorKind, message: str) -> dict[str, Any]:
    """Dispatch on error kind: TERMINAL returns a dict, others raise ModelRetry."""
    if kind == ToolErrorKind.TERMINAL:
        return terminal_error(message)
    raise ModelRetry(message)


# ---------------------------------------------------------------------------
# Google error classification
# ---------------------------------------------------------------------------


def classify_google_error(e: Exception) -> tuple[ToolErrorKind, str]:
    """Classify a Google API exception into (kind, message).

    Inspects HttpError status codes when available, otherwise falls back
    to string matching on the exception message.
    """
    msg = str(e)

    # googleapiclient.errors.HttpError has .status_code or .resp.status
    status = getattr(e, "status_code", None)
    if status is None:
        resp = getattr(e, "resp", None)
        if resp is not None:
            status = int(getattr(resp, "status", 0))

    if status:
        if status in (401, 403):
            return ToolErrorKind.TERMINAL, f"Google: authentication error ({status}). Check credentials."
        if status == 404:
            return ToolErrorKind.MISUSE, f"Google: resource not found (404). Verify the ID and try again."
        if status == 429:
            return ToolErrorKind.TRANSIENT, f"Google: rate limited (429). Wait a moment and retry."
        if status >= 500:
            return ToolErrorKind.TRANSIENT, f"Google: server error ({status}). Retry shortly."

    # String-based fallbacks for errors without status codes
    if "has not been enabled" in msg or "accessnotconfigured" in msg.lower():
        return ToolErrorKind.TERMINAL, msg
    if "invalid" in msg.lower() and ("id" in msg.lower() or "identifier" in msg.lower()):
        return ToolErrorKind.MISUSE, f"Google: {msg}"

    # Default to transient — lets the model retry
    return ToolErrorKind.TRANSIENT, f"Google: API error ({msg}). Check credentials and API quota."


# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------


GOOGLE_NOT_CONFIGURED = (
    "{service}: not configured. "
    "Set google_credentials_path in settings or run: "
    "gcloud auth application-default login"
)

GOOGLE_API_NOT_ENABLED = (
    "{service}: API is not enabled for your project. "
    "Run: gcloud services enable {api_id}"
)


def google_api_error(service: str, error: Exception) -> str:
    """Format a generic Google API error with tool prefix and hint."""
    return f"{service}: API error ({error}). Check credentials and API quota."


def terminal_error(message: str) -> dict[str, Any]:
    """Return an error dict for terminal (non-retryable) tool failures.

    Unlike ModelRetry, this stops the retry loop immediately — the model
    sees the error in the tool result and can pick a different tool.
    """
    return {"display": message, "error": True}
