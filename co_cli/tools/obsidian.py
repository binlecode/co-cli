"""Obsidian vault tools using RunContext pattern."""

import re
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps


def _extract_frontmatter_tags(content: str) -> set[str]:
    """Extract tags from YAML frontmatter (tags: [...] or tags: [inline])."""
    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not fm_match:
        return set()
    fm = fm_match.group(1)
    # Match "tags:" line(s) — supports list or inline formats
    tags: set[str] = set()
    for m in re.finditer(r"(?:^tags:\s*\[([^\]]*)\]|^\s*-\s*(#?\S+))", fm, re.MULTILINE):
        if m.group(1) is not None:
            # Inline: tags: [foo, bar]
            for t in m.group(1).split(","):
                t = t.strip().strip("'\"")
                if t:
                    tags.add(t if t.startswith("#") else f"#{t}")
        elif m.group(2) is not None:
            # List item:  - foo
            t = m.group(2).strip().strip("'\"")
            if t:
                tags.add(t if t.startswith("#") else f"#{t}")
    return tags


def _snippet_around(content: str, match: re.Match, radius: int = 60) -> str:
    """Extract a snippet around a regex match, breaking at word boundaries."""
    start = max(0, match.start() - radius)
    end = min(len(content), match.end() + radius)
    # Expand to word boundaries
    if start > 0:
        space = content.rfind(" ", start - 20, match.start())
        if space != -1:
            start = space + 1
    if end < len(content):
        space = content.find(" ", match.end(), end + 20)
        if space != -1:
            end = space
    snippet = content[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet


def search_notes(
    ctx: RunContext[CoDeps],
    query: str,
    limit: int = 10,
    folder: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Search note contents for keywords.

    Args:
        query: Space-separated keywords (AND logic, whole words, case-insensitive).
               Example: "project timeline" finds notes containing both words.
        limit: Maximum results to return (default 10).
        folder: Optional subfolder to restrict search (e.g. 'Work/' or 'Projects/2026').
        tag: Optional tag to filter by (e.g. '#project'). Checks YAML frontmatter tags.

    Returns:
        Dict with ``display`` (pre-formatted), ``count``, and ``has_more``.
    """
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian: vault not configured or not found. "
            "Set obsidian_vault_path in settings."
        )

    # Parse keywords (split on whitespace, filter empty)
    keywords = [k.strip() for k in query.split() if k.strip()]
    if not keywords:
        raise ModelRetry("Obsidian: empty query. Provide keywords to search.")

    # Build word-boundary patterns for each keyword
    patterns = [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in keywords]

    # Normalize tag filter
    if tag and not tag.startswith("#"):
        tag = f"#{tag}"

    # Determine search root
    search_root = vault / folder if folder else vault

    # TODO: Replace early exit with SearchDB (see docs/TODO-cross-tool-rag.md)
    results = []
    has_more = False
    for note in search_root.rglob("*.md"):
        if len(results) >= limit + 1:
            break

        try:
            content = note.read_text(encoding="utf-8")

            # Tag filter — check frontmatter and inline tags
            if tag:
                fm_tags = _extract_frontmatter_tags(content)
                if tag not in fm_tags and tag not in content:
                    continue

            # Check ALL keywords match (AND logic)
            matches = [p.search(content) for p in patterns]
            if not all(matches):
                continue

            results.append({
                "file": str(note.relative_to(vault)),
                "snippet": _snippet_around(content, matches[0]),
            })
        except Exception:
            continue

    # Check if there are more results beyond limit
    if len(results) > limit:
        has_more = True
        results = results[:limit]

    if not results:
        return {
            "display": f"No notes found matching: {' '.join(keywords)}",
            "count": 0,
            "has_more": False,
        }

    lines = []
    for r in results:
        lines.append(f"**{r['file']}**")
        lines.append(f"  {r['snippet']}")
        lines.append("")

    return {
        "display": "\n".join(lines).rstrip(),
        "count": len(results),
        "has_more": has_more,
    }


def list_notes(ctx: RunContext[CoDeps], tag: str | None = None) -> dict[str, Any]:
    """List all markdown notes in the Obsidian vault.

    Args:
        tag: Optional tag to filter by (e.g. '#project').

    Returns:
        Dict with ``display`` (pre-formatted) and ``count``.
    """
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian: vault not configured or not found. "
            "Set obsidian_vault_path in settings."
        )

    notes = list(vault.rglob("*.md"))

    if tag:
        note_paths = []
        for note in notes:
            try:
                content = note.read_text(encoding="utf-8")
                if tag in content:
                    note_paths.append(str(note.relative_to(vault)))
            except Exception:
                continue
    else:
        note_paths = [str(note.relative_to(vault)) for note in notes]

    if not note_paths:
        label = f" with tag {tag}" if tag else ""
        return {
            "display": f"No notes found{label}.",
            "count": 0,
        }

    display = "\n".join(f"- {p}" for p in note_paths)
    return {
        "display": display,
        "count": len(note_paths),
    }


def read_note(ctx: RunContext[CoDeps], filename: str) -> str:
    """Read the content of a specific note from the Obsidian vault.

    Args:
        filename: Relative path to the note (e.g. 'Work/Project X.md').
    """
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian: vault not configured or not found. "
            "Set obsidian_vault_path in settings."
        )

    # Sanitize path to prevent directory traversal
    safe_path = (vault / filename).resolve()
    if not safe_path.is_relative_to(vault.resolve()):
        raise ModelRetry("Obsidian: access denied — path is outside the vault.")

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
        raise ModelRetry(f"Obsidian: error reading note ({e}).")
