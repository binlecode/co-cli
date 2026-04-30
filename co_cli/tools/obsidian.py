"""Obsidian vault tools using RunContext pattern."""

import re
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.memory.search_util import snippet_around
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output


def _obsidian_available(deps: CoDeps) -> bool:
    return deps.obsidian_vault_path is not None and deps.obsidian_vault_path.exists()


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


def _format_note_result(file_path: str, snippet: str | None) -> list[str]:
    """Format one note search result into display lines."""
    lines = [f"**{file_path}**"]
    if snippet:
        lines.append(f"  {snippet}")
    lines.append("")
    return lines


def _fts_search_notes(
    ctx: RunContext[CoDeps],
    vault: Path,
    search_root: Path,
    query: str,
    keywords: list[str],
    tag: str | None,
    limit: int,
) -> ToolReturn | None:
    """FTS5 search path. Returns ToolReturn on success, None to fall through to regex."""
    try:
        ctx.deps.knowledge_store.sync_dir("obsidian", search_root)
        fts_results = ctx.deps.knowledge_store.search(
            query,
            source="obsidian",
            limit=limit + 1,
        )
        if search_root != vault:
            search_root_str = str(search_root)
            fts_results = [
                r
                for r in fts_results
                if r.path == search_root_str or r.path.startswith(search_root_str + "/")
            ]
        has_more = len(fts_results) > limit
        fts_results = fts_results[:limit]
        if not fts_results:
            return tool_output(
                f"No notes found matching: {' '.join(keywords)}",
                ctx=ctx,
                count=0,
                has_more=False,
            )
        lines: list[str] = []
        for r in fts_results:
            rel_path = r.path
            try:
                rel_path = str(Path(r.path).relative_to(vault))
            except ValueError:
                pass
            lines.extend(_format_note_result(rel_path, r.snippet or None))
        return tool_output(
            "\n".join(lines).rstrip(),
            ctx=ctx,
            count=len(fts_results),
            has_more=has_more,
        )
    except Exception:
        return None


def _grep_search_notes(
    ctx: RunContext[CoDeps],
    vault: Path,
    search_root: Path,
    keywords: list[str],
    tag: str | None,
    limit: int,
) -> ToolReturn:
    """Regex fallback search path."""
    patterns = [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in keywords]
    results: list[dict[str, str]] = []
    for note in search_root.rglob("*.md"):
        if len(results) >= limit + 1:
            break
        try:
            content = note.read_text(encoding="utf-8")
            if tag:
                fm_tags = _extract_frontmatter_tags(content)
                if tag not in fm_tags and tag not in content:
                    continue
            matches = [p.search(content) for p in patterns]
            first_match = matches[0] if matches else None
            if not all(matches) or first_match is None:
                continue
            results.append(
                {
                    "file": str(note.relative_to(vault)),
                    "snippet": snippet_around(content, first_match),
                }
            )
        except Exception:
            continue
    has_more = len(results) > limit
    results = results[:limit]
    if not results:
        return tool_output(
            f"No notes found matching: {' '.join(keywords)}",
            ctx=ctx,
            count=0,
            has_more=False,
        )
    lines: list[str] = []
    for r in results:
        lines.extend(_format_note_result(r["file"], r["snippet"]))
    return tool_output(
        "\n".join(lines).rstrip(),
        ctx=ctx,
        count=len(results),
        has_more=has_more,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_read_only=True,
    is_concurrent_safe=True,
    integration="obsidian",
    requires_config="obsidian_vault_path",
    check_fn=_obsidian_available,
)
def obsidian_search(
    ctx: RunContext[CoDeps],
    query: str,
    limit: int = 10,
    folder: str | None = None,
    tag: str | None = None,
) -> ToolReturn:
    """Search Obsidian vault notes by keyword. All keywords must match
    (AND logic, whole words, case-insensitive). Returns matching filenames
    with text snippets around the first match.

    To read the full content of a matched note, pass its filename to obsidian_read.

    Narrow results with folder or tag filters when the vault is large or the
    query is broad.

    This tool searches the user's Obsidian note vault (local markdown files).
    For stored preferences and decisions, use memory_search instead. For cloud
    documents, use google_drive_search.

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
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian: vault not configured or not found. Set obsidian_vault_path in settings."
        )
    keywords = [k.strip() for k in query.split() if k.strip()]
    if not keywords:
        raise ModelRetry("Obsidian: empty query. Provide keywords to search.")
    if tag and not tag.startswith("#"):
        tag = f"#{tag}"
    search_root = vault / folder if folder else vault

    if ctx.deps.knowledge_store is not None:
        result = _fts_search_notes(ctx, vault, search_root, query, keywords, tag, limit)
        if result is not None:
            return result

    return _grep_search_notes(ctx, vault, search_root, keywords, tag, limit)


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_read_only=True,
    is_concurrent_safe=True,
    integration="obsidian",
    requires_config="obsidian_vault_path",
    check_fn=_obsidian_available,
)
def obsidian_list(
    ctx: RunContext[CoDeps],
    tag: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> ToolReturn:
    """List markdown note filenames in the Obsidian vault. Returns one page
    at a time (default 20 per page).

    Use this for a directory overview or to discover note paths before calling
    obsidian_read. For keyword search within note content, use obsidian_search
    instead. For stored preferences and decisions, use memory_list.

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
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian: vault not configured or not found. Set obsidian_vault_path in settings."
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
        return tool_output(
            f"No notes found{label}.",
            ctx=ctx,
            count=0,
            total=0,
            offset=offset,
            limit=limit,
            has_more=False,
        )

    # Stable sort for consistent pagination
    note_paths.sort()
    total = len(note_paths)

    # Paginate
    page = note_paths[offset : offset + limit]
    has_more = offset + limit < total

    lines = [f"- {p}" for p in page]
    if has_more:
        lines.append(
            f"\nShowing {offset + 1}\u2013{offset + len(page)} of {total}. "
            f"More available \u2014 call with offset={offset + limit}."
        )

    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(page),
        total=total,
        offset=offset,
        limit=limit,
        has_more=has_more,
    )


@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_read_only=True,
    is_concurrent_safe=True,
    integration="obsidian",
    requires_config="obsidian_vault_path",
    check_fn=_obsidian_available,
)
def obsidian_read(ctx: RunContext[CoDeps], filename: str) -> ToolReturn:
    """Read the full markdown content of a note from the Obsidian vault.

    Use filenames from obsidian_search or obsidian_list results. Do not guess paths.

    Returns the raw markdown text including any YAML frontmatter.

    Caveats:
    - Only reads files inside the configured vault directory (path traversal
      is blocked)
    - If the note is not found, the error message lists available notes

    Args:
        filename: Relative path within the vault (e.g. "Work/Project X.md").
                  Use exact paths from obsidian_search or obsidian_list.
    """
    vault = ctx.deps.obsidian_vault_path
    if not vault or not vault.exists():
        raise ModelRetry(
            "Obsidian: vault not configured or not found. Set obsidian_vault_path in settings."
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
            f"Available notes: {available}. Use exact path from obsidian_list."
        )

    try:
        text = safe_path.read_text(encoding="utf-8")
    except Exception as e:
        raise ModelRetry(f"Obsidian: error reading note ({e}).") from e

    return tool_output(
        text,
        ctx=ctx,
        path=str(safe_path.relative_to(vault)),
    )
