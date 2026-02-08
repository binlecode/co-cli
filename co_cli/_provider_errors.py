"""Chat-loop error classification for LLM provider responses.

Classifies ModelHTTPError / ModelAPIError into actions the chat loop
can dispatch on: reflect (400), backoff-retry (429/5xx/network), or abort.
"""

import enum
import re

from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError


class ProviderErrorAction(enum.Enum):
    REFLECT       = "reflect"         # 400 — inject error text, re-run
    BACKOFF_RETRY = "backoff_retry"   # 429/5xx/network — wait, re-run
    ABORT         = "abort"           # 401/403/404 — end turn


def _parse_retry_after(body: object | None) -> float:
    """Extract Retry-After seconds from an error body, default 3.0.

    Handles both numeric strings and common JSON error body formats
    where the value may appear as a header echo or nested field.
    """
    if body is None:
        return 3.0
    text = str(body)
    # Look for "retry-after": "N" or retry_after: N patterns
    match = re.search(r'retry[_-]after["\s:]+(\d+(?:\.\d+)?)', text, re.IGNORECASE)
    if match:
        return min(float(match.group(1)), 60.0)
    return 3.0


def classify_provider_error(
    e: ModelHTTPError | ModelAPIError,
) -> tuple[ProviderErrorAction, str, float]:
    """Classify a provider error into (action, message, delay_seconds).

    Returns:
        action: What the chat loop should do.
        message: Human-readable description for logging.
        delay: Seconds to wait before retrying (0 for ABORT/REFLECT).
    """
    if isinstance(e, ModelHTTPError):
        code = e.status_code
        if code == 400:
            return ProviderErrorAction.REFLECT, str(e.body or e), 0.5
        if code in (401, 403):
            return ProviderErrorAction.ABORT, f"Authentication error ({code}): {e.body}", 0.0
        if code == 404:
            return ProviderErrorAction.ABORT, f"Model not found ({code}): {e.body}", 0.0
        if code == 429:
            delay = _parse_retry_after(e.body)
            return ProviderErrorAction.BACKOFF_RETRY, f"Rate limited ({code})", delay
        if code >= 500:
            return ProviderErrorAction.BACKOFF_RETRY, f"Server error ({code})", 2.0
        # Unknown 4xx — treat as abort
        return ProviderErrorAction.ABORT, f"Client error ({code}): {e.body}", 0.0

    # ModelAPIError (network/timeout/unknown)
    return ProviderErrorAction.BACKOFF_RETRY, f"Network error: {e}", 2.0
