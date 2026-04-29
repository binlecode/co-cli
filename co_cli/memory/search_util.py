"""Shared search utilities for knowledge retrieval tools."""

import re


def sanitize_fts5_query(query: str) -> str:
    """Sanitize a user query for safe use in FTS5 MATCH expressions.

    Preserves intentional FTS5 syntax (quoted phrases, boolean operators,
    prefix wildcards) while fixing the common failure modes that cause
    sqlite3.OperationalError: unmatched quotes, stray +/(){}^ operators,
    dangling AND/OR/NOT, and hyphenated/dotted/underscored terms that
    FTS5's porter tokenizer would otherwise split incorrectly.
    """
    # Step 1: Protect balanced quoted phrases via numbered placeholders.
    _quoted: list[str] = []

    def _keep_quoted(m: re.Match) -> str:
        _quoted.append(m.group(0))
        return f"\x00Q{len(_quoted) - 1}\x00"

    sanitized = re.sub(r'"[^"]*"', _keep_quoted, query)

    # Step 2: Strip remaining FTS5-special chars that cause parse errors.
    sanitized = re.sub(r"[+{}()\"^]", " ", sanitized)

    # Step 3: Collapse repeated * and remove leading * (no valid prefix target).
    sanitized = re.sub(r"\*+", "*", sanitized)
    sanitized = re.sub(r"(^|\s)\*", r"\1", sanitized)

    # Step 4: Remove dangling boolean operators at start or end.
    sanitized = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", sanitized.strip())
    sanitized = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", sanitized.strip())

    # Step 5: Quote unquoted hyphenated/dotted/underscored terms so FTS5
    # treats them as exact phrases rather than splitting on the separator.
    # e.g. "chat-send" → '"chat-send"', "session_store.py" → '"session_store.py"'
    sanitized = re.sub(r"\b(\w+(?:[._-]\w+)+)\b", r'"\1"', sanitized)

    # Step 6: Restore protected quoted phrases.
    for i, phrase in enumerate(_quoted):
        sanitized = sanitized.replace(f"\x00Q{i}\x00", phrase)

    return sanitized.strip()


def snippet_around(content: str, match: re.Match, radius: int = 60) -> str:
    """Extract a snippet around a regex match, expanding to word boundaries."""
    start = max(0, match.start() - radius)
    end = min(len(content), match.end() + radius)
    if start > 0:
        space = content.rfind(" ", start - 20, match.start())
        if space != -1:
            start = space + 1
    if end < len(content):
        space = content.find(" ", match.end(), end + 20)
        if space != -1:
            end = space
    snip = content[start:end].replace("\n", " ").strip()
    if start > 0:
        snip = "..." + snip
    if end < len(content):
        snip = snip + "..."
    return snip
