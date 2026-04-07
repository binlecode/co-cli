"""Native file system tools: list, read, find, write, edit."""

import fnmatch
import re
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext

from co_cli.deps import CoDeps
from co_cli.tools.tool_errors import tool_error
from pydantic_ai.messages import ToolReturn

from co_cli.tools.tool_output import tool_output


def _resolve_workspace_path(raw: str, workspace_root: Path) -> Path:
    """Resolve path and verify it stays within workspace_root.

    Raises ValueError if path escapes workspace.
    """
    resolved = (workspace_root / raw).resolve()
    if not resolved.is_relative_to(workspace_root.resolve()):
        raise ValueError(f"Path escapes workspace: {raw}")
    return resolved


async def list_directory(
    ctx: RunContext[CoDeps],
    path: str = ".",
    pattern: str = "*",
    max_entries: int = 200,
) -> ToolReturn:
    """List directory contents, optionally filtered by a glob pattern.

    Returns files and subdirectories matching the pattern up to max_entries.
    Each entry is prefixed with [dir] or [file] to indicate type.

    Args:
        path: Directory path relative to the workspace root (default: current directory).
        pattern: Glob pattern to filter entries (default: "*" matches all).
        max_entries: Maximum number of entries to return (default: 200).
    """
    try:
        resolved = _resolve_workspace_path(path, ctx.deps.config.workspace_root)
    except ValueError as e:
        return tool_error(str(e))

    if not resolved.exists():
        return tool_error(f"Path not found: {path}")

    if not resolved.is_dir():
        return tool_error(f"Not a directory: {path}")

    entries: list[dict[str, str]] = []
    for entry in sorted(resolved.iterdir()):
        if not fnmatch.fnmatch(entry.name, pattern):
            continue
        kind = "dir" if entry.is_dir() else "file"
        entries.append({"name": entry.name, "type": kind})
        if len(entries) >= max_entries:
            break

    lines = [f"[{e['type']}] {e['name']}" for e in entries]
    display = "\n".join(lines) if lines else "(empty)"

    return tool_output(
        display,
        ctx=ctx,
        path=str(resolved),
        count=len(entries),
        entries=entries,
    )


async def read_file(
    ctx: RunContext[CoDeps],
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ToolReturn:
    """Read a file's contents, optionally restricted to a line range.

    Line numbers are 1-indexed and inclusive. If start_line/end_line are omitted,
    the full file is returned.

    Args:
        path: File path relative to the workspace root.
        start_line: First line to include (1-indexed, inclusive). Optional.
        end_line: Last line to include (1-indexed, inclusive). Optional.
    """
    try:
        resolved = _resolve_workspace_path(path, ctx.deps.config.workspace_root)
    except ValueError as e:
        return tool_error(str(e))

    if not resolved.exists():
        return tool_error(f"File not found: {path}")

    if resolved.is_dir():
        return tool_error(f"Path is a directory: {path}")

    content = resolved.read_text(encoding="utf-8")
    all_lines = content.splitlines(keepends=True)
    total_line_count = len(all_lines)

    if start_line is not None or end_line is not None:
        # Slice is 0-indexed; start_line/end_line are 1-indexed inclusive
        lo = (start_line - 1) if start_line is not None else 0
        hi = end_line if end_line is not None else total_line_count
        sliced = all_lines[lo:hi]
        display = "".join(sliced)
    else:
        display = content

    return tool_output(
        display,
        ctx=ctx,
        path=str(resolved),
        lines=total_line_count,
    )


async def find_in_files(
    ctx: RunContext[CoDeps],
    pattern: str,
    glob: str = "**/*",
    max_matches: int = 50,
) -> ToolReturn:
    """Search for a regex pattern across files in the workspace.

    Skips binary files. Returns up to max_matches results formatted as
    file:line: text.

    Args:
        pattern: Regular expression to search for.
        glob: Glob pattern to filter which files are searched (default: "**/*").
        max_matches: Maximum number of matching lines to return (default: 50).
    """
    try:
        compiled = re.compile(pattern)
    except re.error:
        return tool_error(f"Invalid regex: {pattern}")

    workspace_root = ctx.deps.config.workspace_root
    matches: list[dict[str, Any]] = []
    lines_output: list[str] = []

    for file_path in workspace_root.glob(glob):
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Skip binary files
            continue

        rel_path = str(file_path.relative_to(workspace_root))
        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                matches.append({"file": rel_path, "line": line_no, "text": line})
                lines_output.append(f"{rel_path}:{line_no}: {line}")
                if len(matches) >= max_matches:
                    break
        if len(matches) >= max_matches:
            break

    display = "\n".join(lines_output) if lines_output else "(no matches)"

    return tool_output(
        display,
        ctx=ctx,
        pattern=pattern,
        count=len(matches),
        matches=matches,
    )


async def write_file(
    ctx: RunContext[CoDeps],
    path: str,
    content: str,
) -> ToolReturn:
    """Write content to a file, creating parent directories as needed.

    Overwrites the file if it already exists.

    Args:
        path: File path relative to the workspace root.
        content: Text content to write.
    """
    try:
        resolved = _resolve_workspace_path(path, ctx.deps.config.workspace_root)
    except ValueError as e:
        return tool_error(str(e))

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    byte_count = len(content.encode("utf-8"))

    return tool_output(
        f"Written: {path} ({byte_count} bytes)",
        path=str(resolved),
        bytes=byte_count,
    )


async def edit_file(
    ctx: RunContext[CoDeps],
    path: str,
    search: str,
    replacement: str,
    replace_all: bool = False,
) -> ToolReturn:
    """Edit a file by replacing a search string with a replacement.

    Raises ValueError if the search string is not found or if there are multiple
    occurrences and replace_all is False.

    Args:
        path: File path relative to the workspace root.
        search: Exact string to search for in the file.
        replacement: String to replace the search string with.
        replace_all: If True, replace all occurrences; otherwise requires exactly one.
    """
    try:
        resolved = _resolve_workspace_path(path, ctx.deps.config.workspace_root)
    except ValueError as e:
        return tool_error(str(e))

    if not resolved.exists():
        return tool_error(f"File not found: {path}")

    from co_cli.tools.resource_lock import ResourceBusyError
    try:
        async with ctx.deps.resource_locks.try_acquire(str(resolved)):
            content = resolved.read_text(encoding="utf-8")
            count = content.count(search)

            if count == 0:
                raise ValueError(f"Search string not found in {path}: {search!r}")

            if count > 1 and not replace_all:
                raise ValueError(
                    f"Found {count} occurrences of search string in {path}; use replace_all=True to replace all"
                )

            updated = content.replace(search, replacement)
            resolved.write_text(updated, encoding="utf-8")

            return tool_output(
                f"Edited: {path} ({count} replacement(s))",
                path=str(resolved),
                replacements=count,
            )
    except ResourceBusyError:
        return tool_error(f"File {path} is being modified by another tool call — retry next turn")
