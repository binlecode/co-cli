"""Shared search utilities for knowledge retrieval tools."""

import re


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
