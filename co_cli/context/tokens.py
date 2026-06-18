"""Shared token-estimation helper."""

from co_cli.config.tuning import ESTIMATE_CHARS_PER_TOKEN


def estimate_text_tokens(text: str) -> int:
    """Rough token estimate for a raw string: ~4 chars/token."""
    return len(text) // ESTIMATE_CHARS_PER_TOKEN
