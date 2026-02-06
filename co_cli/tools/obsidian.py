"""Obsidian vault tools using RunContext pattern."""

import re
from pathlib import Path

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps


def search_notes(ctx: RunContext[CoDeps], query: str, limit: int = 10) -> list[dict]:
    """Search note contents for keywords.

    Args:
        query: Space-separated keywords (AND logic, whole words, case-insensitive).
               Example: "project timeline" finds notes containing both words.
        limit: Maximum results to return (default 10).

    Returns:
        List of matches with filename and context snippet.
    """
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian vault not configured or not found. "
            "Ask user to set obsidian_vault_path in settings."
        )

    # Parse keywords (split on whitespace, filter empty)
    keywords = [k.strip() for k in query.split() if k.strip()]
    if not keywords:
        raise ModelRetry("Empty query. Provide keywords to search.")

    # Build word-boundary patterns for each keyword
    patterns = [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in keywords]

    # TODO: Replace early exit with hybrid search (see docs/TODO-obsidian-search.md)
    results = []
    for note in vault.rglob("*.md"):
        if len(results) >= limit:
            break  # Early exit - no reranker yet

        try:
            content = note.read_text(encoding="utf-8")

            # Check ALL keywords match (AND logic)
            matches = [p.search(content) for p in patterns]
            if not all(matches):
                continue

            # Use first match for snippet context
            first_match = matches[0]
            start = max(0, first_match.start() - 50)
            end = min(len(content), first_match.end() + 50)
            snippet = content[start:end].replace("\n", " ").strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(content):
                snippet = snippet + "..."

            results.append({
                "file": str(note.relative_to(vault)),
                "snippet": snippet,
            })
        except Exception:
            continue

    if not results:
        raise ModelRetry(
            f"No notes found matching all keywords: {keywords}. "
            "Try fewer or different keywords."
        )

    return results


def list_notes(ctx: RunContext[CoDeps], tag: str | None = None) -> list[str]:
    """List all markdown notes in the Obsidian vault.

    Args:
        tag: Optional tag to filter by (e.g. '#project').
    """
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian vault not configured or not found. "
            "Ask user to set obsidian_vault_path in settings."
        )

    notes = list(vault.rglob("*.md"))

    if tag:
        filtered = []
        for note in notes:
            try:
                content = note.read_text(encoding="utf-8")
                if tag in content:
                    filtered.append(str(note.relative_to(vault)))
            except Exception:
                continue
        return filtered

    return [str(note.relative_to(vault)) for note in notes]


def read_note(ctx: RunContext[CoDeps], filename: str) -> str:
    """Read the content of a specific note from the Obsidian vault.

    Args:
        filename: Relative path to the note (e.g. 'Work/Project X.md').
    """
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian vault not configured or not found. "
            "Ask user to set obsidian_vault_path in settings."
        )

    # Sanitize path to prevent directory traversal
    safe_path = (vault / filename).resolve()
    if not safe_path.is_relative_to(vault.resolve()):
        raise ModelRetry("Access denied: path is outside the vault.")

    if not safe_path.exists():
        # Provide helpful context for retry
        available = [str(n.relative_to(vault)) for n in vault.rglob("*.md")][:10]
        raise ModelRetry(
            f"Note '{filename}' not found. "
            f"Available notes: {available}. Use exact path from list_notes."
        )

    try:
        return safe_path.read_text(encoding="utf-8")
    except Exception as e:
        raise ModelRetry(f"Error reading note: {e}")
