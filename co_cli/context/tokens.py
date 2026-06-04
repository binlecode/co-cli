"""Shared token-estimation constants and helpers."""

CHARS_PER_TOKEN = 4  # fast proxy, not a tokenizer


def estimate_text_tokens(text: str) -> int:
    """Rough token estimate for a raw string: ~4 chars/token."""
    return len(text) // CHARS_PER_TOKEN
