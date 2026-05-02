"""Shared search utilities for knowledge retrieval tools."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def normalize_bm25(rank: float) -> float:
    """Map a raw BM25 rank (negative, lower = better) to a [0, 1) score."""
    return abs(rank) / (1.0 + abs(rank))


def _like_tokens(query: str) -> list[str]:
    """Extract plain search tokens from a (possibly sanitized) FTS5 query string."""
    unwrapped = re.sub(r'"([^"]*)"', lambda m: m.group(1), query)
    unwrapped = re.sub(r'[+*^(){}\[\]"<>]', " ", unwrapped)
    unwrapped = re.sub(r"\b(?:AND|OR|NOT)\b", " ", unwrapped, flags=re.IGNORECASE)
    return [t for t in unwrapped.split() if len(t) >= 2]


def run_fts(
    conn: Any,
    sql: str,
    params: tuple | list,
    *,
    label: str = "FTS",
    like_fallback: Callable[[Any, list[str]], list[Any]] | None = None,
) -> list[Any]:
    """Execute an FTS5 MATCH query, returning rows or [] on OperationalError.

    If like_fallback is provided and FTS5 raises OperationalError, calls
    like_fallback(conn, tokens) where tokens are plain words from params[0].
    """
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:  # type: ignore[attr-defined]
        if like_fallback is not None:
            tokens = _like_tokens(str(params[0]))
            logger.warning(
                "%s FTS error, falling back to LIKE (%d tokens): %s", label, len(tokens), exc
            )
            return like_fallback(conn, tokens) if tokens else []
        logger.warning("%s search error: %s", label, exc)
        return []


def sanitize_fts5_query(query: str) -> str:
    """Sanitize a user query for safe use in FTS5 MATCH expressions.

    Preserves intentional FTS5 syntax (quoted phrases, boolean operators,
    prefix wildcards) while fixing the common failure modes that cause
    sqlite3.OperationalError: unmatched quotes, stray +/(){}^ operators,
    dangling AND/OR/NOT, and hyphenated/dotted/underscored terms that
    FTS5's porter tokenizer would otherwise split incorrectly.

    All 6 steps are necessary — eval_fts_sanitize confirmed that the stripped
    3-step variant (steps 1+2+6 only) produced 15 FTS5 OperationalErrors on a
    34-query set; each step below addresses a specific class of failures.
    """
    # Step 1: Protect balanced quoted phrases via numbered placeholders.
    # Necessary so step 2 does not destroy intentional "phrase search" syntax.
    _quoted: list[str] = []

    def _keep_quoted(m: re.Match) -> str:
        _quoted.append(m.group(0))
        return f"\x00Q{len(_quoted) - 1}\x00"

    sanitized = re.sub(r'"[^"]*"', _keep_quoted, query)

    # Step 2: Strip remaining FTS5-special chars that cause parse errors.
    # +, {}, (), ^  are FTS5 column-filter / boost / grouping syntax that users
    # rarely intend; leaving them in triggers OperationalError.
    sanitized = re.sub(r"[+{}()\"^]", " ", sanitized)

    # Step 3: Collapse repeated * and remove leading * (no valid prefix target).
    # "asyncio**" → OperationalError; "* asyncio" → "unknown special query" error.
    # Without this step: 2 additional FTS5 failures in the eval set.
    sanitized = re.sub(r"\*+", "*", sanitized)
    sanitized = re.sub(r"(^|\s)\*", r"\1", sanitized)

    # Step 4: Remove dangling boolean operators at start or end.
    # "AND asyncio", "asyncio OR", "NOT asyncio" all raise OperationalError when
    # the operator has no left- or right-hand operand.
    # Without this step: 4 additional FTS5 failures in the eval set.
    sanitized = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", sanitized.strip())
    sanitized = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", sanitized.strip())

    # Step 5: Quote unquoted hyphenated/dotted/underscored terms so FTS5
    # treats them as exact phrases rather than splitting on the separator.
    # e.g. "chat-send" → '"chat-send"', "session_store.py" → '"session_store.py"'
    # Without this step: 9 additional FTS5 failures in the eval set ("no such
    # column: cli", "fts5: syntax error near '.'", etc.).
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
