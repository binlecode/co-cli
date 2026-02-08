"""Shared ModelRetry message constants for tool error normalization.

All ModelRetry messages follow the format: "{Tool}: {problem}. {Action hint}."
"""

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
