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
    """Search Obsidian vault notes by keyword. All keywords must match
    (AND logic, whole words, case-insensitive). Returns matching filenames
    with text snippets around the first match.

    To read the full content of a matched note, pass its filename to read_note.

    Narrow results with folder or tag filters when the vault is large or the
    query is broad.

    This tool searches the user's Obsidian note vault (local markdown files).
    For stored preferences and decisions, use recall_memory instead. For cloud
    documents, use search_drive_files.

    Returns a dict with:
    - display: pre-formatted results with filenames and snippets — show
      directly to the user
    - count: number of notes matched
    - has_more: true if more results exist beyond the limit

    Caveats:
    - Only searches .md files in the vault
    - Whole-word matching: "test" will not match "testing" or "tests"

    Args:
        query: Space-separated keywords (AND logic, whole words, case-insensitive).
               Example: "project timeline" finds notes containing both words.
        limit: Max results to return (default 10).
        folder: Subfolder to restrict search (e.g. "Work/" or "Projects/2026").
        tag: Tag to filter by (e.g. "#project"). Checks YAML frontmatter tags.
    """
    vault = ctx.deps.config.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian: vault not configured or not found. "
            "Set obsidian_vault_path in settings."
        )

    # Parse keywords (split on whitespace, filter empty)
    keywords = [k.strip() for k in query.split() if k.strip()]
    if not keywords:
        raise ModelRetry("Obsidian: empty query. Provide keywords to search.")

    # Normalize tag filter
    if tag and not tag.startswith("#"):
        tag = f"#{tag}"

    # Determine search root
    search_root = vault / folder if folder else vault

    # FTS path — sync on first use, then search the index
    if ctx.deps.services.knowledge_index is not None:
        try:
            ctx.deps.services.knowledge_index.sync_dir("obsidian", search_root)
            tag_filter = tag.lstrip("#") if tag else None
            fts_results = ctx.deps.services.knowledge_index.search(
                query,
                source="obsidian",
                tags=[tag_filter] if tag_filter else None,
                limit=limit + 1,
            )
            if folder:
                search_root_str = str(search_root)
                fts_results = [
                    r for r in fts_results
                    if r.path == search_root_str or r.path.startswith(search_root_str + "/")
                ]
            has_more = len(fts_results) > limit
            fts_results = fts_results[:limit]

            if not fts_results:
                return {
                    "display": f"No notes found matching: {' '.join(keywords)}",
                    "count": 0,
                    "has_more": False,
                }

            lines = []
            for r in fts_results:
                rel_path = r.path
                try:
                    rel_path = str(Path(r.path).relative_to(vault))
                except ValueError:
                    pass
                lines.append(f"**{rel_path}**")
                if r.snippet:
                    lines.append(f"  {r.snippet}")
                lines.append("")

            return {
                "display": "\n".join(lines).rstrip(),
                "count": len(fts_results),
                "has_more": has_more,
            }
        except Exception:
            pass  # Fall through to regex path

    # Build word-boundary patterns for each keyword (regex fallback)
    patterns = [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in keywords]

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


def list_notes(
    ctx: RunContext[CoDeps],
    tag: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """List markdown note filenames in the Obsidian vault. Returns one page
    at a time (default 20 per page).

    Use this for a directory overview or to discover note paths before calling
    read_note. For keyword search within note content, use search_notes
    instead. For stored preferences and decisions, use list_memories.

    Keep paginating until has_more is false when you need a complete listing.

    Returns a dict with:
    - display: bullet list of relative file paths — show directly to the user
    - count: number of notes in this page
    - total: total number of notes across all pages
    - offset: starting position of this page
    - limit: page size requested
    - has_more: true if more pages exist beyond this one

    Args:
        tag: Filter to notes containing this tag (e.g. "#project").
        offset: Starting position (0-based). Example: offset=20 skips the
                first 20 notes.
        limit: Max notes per page (default 20).
    """
    vault = ctx.deps.config.obsidian_vault_path
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
            "total": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
        }

    # Stable sort for consistent pagination
    note_paths.sort()
    total = len(note_paths)

    # Paginate
    page = note_paths[offset:offset + limit]
    has_more = offset + limit < total

    lines = [f"- {p}" for p in page]
    if has_more:
        lines.append(
            f"\nShowing {offset + 1}\u2013{offset + len(page)} of {total}. "
            f"More available \u2014 call with offset={offset + limit}."
        )

    return {
        "display": "\n".join(lines),
        "count": len(page),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


def read_note(ctx: RunContext[CoDeps], filename: str) -> str:
    """Read the full markdown content of a note from the Obsidian vault.

    Use filenames from search_notes or list_notes results. Do not guess paths.

    Returns the raw markdown text including any YAML frontmatter.

    Caveats:
    - Only reads files inside the configured vault directory (path traversal
      is blocked)
    - If the note is not found, the error message lists available notes

    Args:
        filename: Relative path within the vault (e.g. "Work/Project X.md").
                  Use exact paths from search_notes or list_notes.
    """
    vault = ctx.deps.config.obsidian_vault_path
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
