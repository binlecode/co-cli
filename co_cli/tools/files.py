"""Native file system tools: list, read, find, write, patch."""

import fnmatch
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools.tool_errors import tool_error
from co_cli.tools.tool_output import tool_output


def _enforce_workspace_boundary(path: Path, workspace_root: Path) -> Path:
    """Resolve path against workspace_root and verify it stays within.

    Defense in depth: CoToolLifecycle.before_tool_execute pre-resolves paths,
    but this function handles both pre-resolved (absolute) and raw (relative)
    paths as a safety net.

    Raises ValueError if path escapes workspace.
    """
    resolved = (workspace_root / path).resolve()
    if not resolved.is_relative_to(workspace_root.resolve()):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


_MAX_EDIT_BYTES = 10 * 1024 * 1024  # 10 MB hard block for patch


def _safe_mtime(p: Path) -> float:
    """Return file mtime, falling back to 0.0 for broken symlinks or inaccessible paths."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _detect_encoding(path: Path) -> str:
    """Detect file encoding from BOM prefix — returns 'utf-16' or 'utf-8'."""
    with open(path, "rb") as fh:
        raw = fh.read(2048)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    return "utf-8"


def _is_recursive_pattern(pattern: str) -> bool:
    """Return True when the glob pattern requires recursive traversal."""
    return "**" in pattern or "/" in pattern


async def glob(
    ctx: RunContext[CoDeps],
    path: str = ".",
    pattern: str = "*",
    max_entries: int = 200,
) -> ToolReturn:
    """List directory contents or find files by name pattern (glob).

    Use for file-name and path discovery — when you need to know what files
    exist or find files by extension/name pattern. Use "**/*.ext" for recursive
    search by name (results sorted by modification time, newest first).

    When NOT to use: for content search inside files — use grep instead.

    Args:
        path: Directory path relative to the workspace root (default: current directory).
        pattern: Glob pattern to filter entries (default: "*" matches all).
                 Use "**/*.ext" for recursive file search by name.
        max_entries: Maximum number of entries to return (default: 200).
    """
    try:
        resolved = _enforce_workspace_boundary(Path(path), ctx.deps.workspace_root)
    except ValueError as e:
        return tool_error(str(e))

    if not resolved.exists():
        return tool_error(f"Path not found: {path}")

    if not resolved.is_dir():
        return tool_error(f"Not a directory: {path}")

    workspace_root = ctx.deps.workspace_root
    truncated = False

    if _is_recursive_pattern(pattern):
        # Recursive glob — sorted by mtime (newest first), paths relative to workspace
        raw = sorted(
            resolved.glob(pattern),
            key=_safe_mtime,
            reverse=True,
        )
        entries: list[dict[str, str]] = []
        for entry in raw:
            kind = "dir" if entry.is_dir() else "file"
            try:
                rel = str(entry.relative_to(workspace_root))
            except ValueError:
                rel = str(entry)
            entries.append({"name": rel, "type": kind})
            if len(entries) >= max_entries:
                truncated = True
                break
    else:
        # Shallow listing — sorted alphabetically by name
        entries = []
        for entry in sorted(resolved.iterdir()):
            if not fnmatch.fnmatch(entry.name, pattern):
                continue
            kind = "dir" if entry.is_dir() else "file"
            entries.append({"name": entry.name, "type": kind})
            if len(entries) >= max_entries:
                truncated = True
                break

    lines = [f"[{e['type']}] {e['name']}" for e in entries]
    if truncated:
        lines.append(f"(truncated at {max_entries} entries — use a more specific pattern)")
    display = "\n".join(lines) if lines else "(empty)"

    return tool_output(
        display,
        ctx=ctx,
        path=str(resolved),
        count=len(entries),
        truncated=truncated,
        entries=entries,
    )


async def read_file(
    ctx: RunContext[CoDeps],
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ToolReturn:
    """Read a file's contents for targeted inspection, with optional line range.

    Use for reading known files. Specify start_line/end_line when the relevant
    region is already known to avoid loading the entire file.

    When NOT to use: when the file location is unknown — use glob or
    grep first to locate the file.

    Line numbers are 1-indexed and inclusive. If start_line/end_line are omitted,
    the full file is returned.

    Args:
        path: File path relative to the workspace root.
        start_line: First line to include (1-indexed, inclusive). Optional.
        end_line: Last line to include (1-indexed, inclusive). Optional.
    """
    try:
        resolved = _enforce_workspace_boundary(Path(path), ctx.deps.workspace_root)
    except ValueError as e:
        return tool_error(str(e))

    if not resolved.exists():
        return tool_error(f"File not found: {path}")

    if resolved.is_dir():
        return tool_error(f"Path is a directory: {path}")

    try:
        enc = _detect_encoding(resolved)
        content = resolved.read_text(encoding=enc)
    except UnicodeDecodeError:
        return tool_error(f"Binary file — cannot display as text: {path}")

    ctx.deps.file_read_mtimes[str(resolved)] = resolved.stat().st_mtime
    all_lines = content.splitlines(keepends=True)
    total_line_count = len(all_lines)

    if start_line is not None or end_line is not None:
        lo = (start_line - 1) if start_line is not None else 0
        hi = end_line if end_line is not None else total_line_count
        sliced = all_lines[lo:hi]
    else:
        sliced = all_lines

    # cat -n style: right-justified 6-char line number + tab
    base = start_line if start_line is not None else 1
    display = "".join(f"{base + i:>6}\t{line}" for i, line in enumerate(sliced))

    return tool_output(
        display,
        ctx=ctx,
        path=str(resolved),
        lines=total_line_count,
    )


async def grep(
    ctx: RunContext[CoDeps],
    pattern: str,
    glob: str = "**/*",
    max_matches: int = 50,
) -> ToolReturn:
    """Search file contents by regex pattern across the workspace.

    Use for content search — finding text, symbols, or patterns inside files.
    Prefer this over shell grep/rg for workspace content search.

    When NOT to use: for file-name discovery — use glob with a pattern instead.

    Skips binary files. Returns up to max_matches results as file:line: text.

    Args:
        pattern: Regular expression to search for.
        glob: Glob pattern to filter which files are searched (default: "**/*").
        max_matches: Maximum number of matching lines to return (default: 50).
    """
    try:
        compiled = re.compile(pattern)
    except re.error:
        return tool_error(f"Invalid regex: {pattern}")

    workspace_root = ctx.deps.workspace_root
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
    """Write content to a new file or completely rewrite an existing file.

    Prefer patch for modifying existing files — it targets a specific
    section and avoids accidentally dropping content. Use write_file only for
    creating new files or intentional full rewrites. Read the file first before
    rewriting to confirm you are not losing content.

    Creates parent directories as needed. Overwrites the file if it already exists.

    Args:
        path: File path relative to the workspace root.
        content: Text content to write.
    """
    try:
        resolved = _enforce_workspace_boundary(Path(path), ctx.deps.workspace_root)
    except ValueError as e:
        return tool_error(str(e))

    from co_cli.tools.resource_lock import ResourceBusyError

    try:
        async with ctx.deps.resource_locks.try_acquire(str(resolved)):
            path_key = str(resolved)
            if (
                path_key in ctx.deps.file_read_mtimes
                and _safe_mtime(resolved) != ctx.deps.file_read_mtimes[path_key]
            ):
                return tool_error("File changed since last read — re-read before writing")
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            byte_count = len(content.encode("utf-8"))
            ctx.deps.file_read_mtimes[path_key] = _safe_mtime(resolved)
            return tool_output(
                f"Written: {path} ({byte_count} bytes)",
                ctx=ctx,
                path=str(resolved),
                bytes=byte_count,
            )
    except ResourceBusyError:
        return tool_error(f"File {path} is being modified by another tool call — retry next turn")


def _transform_line_trimmed(text: str) -> tuple[str, list[int]]:
    """Strip leading/trailing whitespace per line (line endings preserved)."""
    result: list[str] = []
    offsets: list[int] = []
    pos = 0
    for raw_line in text.splitlines(keepends=True):
        if raw_line.endswith("\r\n"):
            content, line_end = raw_line[:-2], "\r\n"
        elif raw_line.endswith(("\n", "\r")):
            content, line_end = raw_line[:-1], raw_line[-1]
        else:
            content, line_end = raw_line, ""
        lstripped = content.lstrip()
        lead_skip = len(content) - len(lstripped)
        rstripped = lstripped.rstrip()
        for idx, ch in enumerate(rstripped):
            result.append(ch)
            offsets.append(pos + lead_skip + idx)
        for idx, ch in enumerate(line_end):
            result.append(ch)
            offsets.append(pos + len(content) + idx)
        pos += len(raw_line)
    return "".join(result), offsets


def _transform_indent_stripped(text: str) -> tuple[str, list[int]]:
    """Strip leading whitespace per line (trailing whitespace and line endings preserved)."""
    result: list[str] = []
    offsets: list[int] = []
    pos = 0
    for raw_line in text.splitlines(keepends=True):
        if raw_line.endswith("\r\n"):
            content, line_end = raw_line[:-2], "\r\n"
        elif raw_line.endswith(("\n", "\r")):
            content, line_end = raw_line[:-1], raw_line[-1]
        else:
            content, line_end = raw_line, ""
        lstripped = content.lstrip()
        lead_skip = len(content) - len(lstripped)
        for idx, ch in enumerate(lstripped):
            result.append(ch)
            offsets.append(pos + lead_skip + idx)
        for idx, ch in enumerate(line_end):
            result.append(ch)
            offsets.append(pos + len(content) + idx)
        pos += len(raw_line)
    return "".join(result), offsets


def _transform_escape_expanded(text: str) -> tuple[str, list[int]]:
    """Expand literal \\n \\t \\r escape sequences to actual characters."""
    _ESC_MAP = {"n": "\n", "t": "\t", "r": "\r"}
    result: list[str] = []
    offsets: list[int] = []
    idx = 0
    while idx < len(text):
        if idx + 1 < len(text) and text[idx] == "\\" and text[idx + 1] in _ESC_MAP:
            result.append(_ESC_MAP[text[idx + 1]])
            offsets.append(idx)
            idx += 2
        else:
            result.append(text[idx])
            offsets.append(idx)
            idx += 1
    return "".join(result), offsets


_FUZZY_STRATEGIES: list[tuple[str, Callable[[str], tuple[str, list[int]]]]] = [
    ("line-trimmed", _transform_line_trimmed),
    ("indent-stripped", _transform_indent_stripped),
    ("escape-expanded", _transform_escape_expanded),
]


def _fuzzy_apply(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
    transform_fn: Callable[[str], tuple[str, list[int]]],
    strategy_name: str,
) -> tuple[str, int] | str | None:
    """Try one fuzzy strategy. Returns (updated_content, count), an error str, or None if no match.

    Extends each match's orig_start backward to include any stripped leading whitespace
    so new_string is applied at the true start of the matched block in the original.
    """
    t_content, offsets = transform_fn(content)
    t_old, _ = transform_fn(old_string)
    if not t_old:
        return None

    matches: list[tuple[int, int]] = []
    search_start = 0
    while True:
        pos = t_content.find(t_old, search_start)
        if pos == -1:
            break
        orig_start = offsets[pos]
        orig_end = offsets[pos + len(t_old) - 1] + 1
        # Extend orig_start backward to include stripped leading whitespace of the
        # first matched line, so new_string is placed at the true line boundary.
        line_boundary = content.rfind("\n", 0, orig_start)
        line_boundary = 0 if line_boundary == -1 else line_boundary + 1
        if not content[line_boundary:orig_start].strip():
            orig_start = line_boundary
        matches.append((orig_start, orig_end))
        search_start = pos + len(t_old)

    if not matches:
        return None

    if len(matches) > 1 and not replace_all:
        return (
            f"Found {len(matches)} occurrences using {strategy_name!r} strategy — "
            "use replace_all=True to replace all"
        )

    result = content
    for orig_start, orig_end in reversed(matches):
        result = result[:orig_start] + new_string + result[orig_end:]
    return result, len(matches)


async def patch(
    ctx: RunContext[CoDeps],
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> ToolReturn:
    """Edit a file by replacing old_string with new_string, with fuzzy matching fallback.

    Use for targeted modifications to existing files. Read the file first to
    understand its content. Use the smallest unique old_string that
    unambiguously identifies the edit location. Set replace_all=True only
    when every occurrence should be replaced.

    Tries four matching strategies in order: exact, line-trimmed (whitespace
    per line), indent-stripped (leading whitespace), escape-expanded (\\n \\t \\r).
    The first strategy that matches exactly once (or all when replace_all=True)
    is applied. Returns an error if all strategies fail or if multiple matches
    are found without replace_all=True.

    When NOT to use: for creating new files or complete rewrites — use
    write_file instead.

    Args:
        path: File path relative to the workspace root.
        old_string: String to replace (supports fuzzy matching).
        new_string: Replacement string (applied verbatim).
        replace_all: If True, replace all occurrences; otherwise requires exactly one.
    """
    try:
        resolved = _enforce_workspace_boundary(Path(path), ctx.deps.workspace_root)
    except ValueError as e:
        return tool_error(str(e))

    if not resolved.exists():
        return tool_error(f"File not found: {path}")

    # Staleness check before acquiring lock — fail-fast, no lock held on stale error
    path_key = str(resolved)
    if (
        path_key in ctx.deps.file_read_mtimes
        and _safe_mtime(resolved) != ctx.deps.file_read_mtimes[path_key]
    ):
        return tool_error("File changed since last read — re-read before writing")

    from co_cli.tools.resource_lock import ResourceBusyError

    try:
        async with ctx.deps.resource_locks.try_acquire(str(resolved)):
            if resolved.stat().st_size > _MAX_EDIT_BYTES:
                return tool_error(
                    f"File too large to edit in-place ({resolved.stat().st_size // 1024} KB) — use shell tools"
                )
            enc = _detect_encoding(resolved)
            content = resolved.read_text(encoding=enc)

            # Strategy 1: exact match (fast path)
            count = content.count(old_string)
            if count > 0:
                if count > 1 and not replace_all:
                    return tool_error(
                        f"Found {count} occurrences in {path} — use replace_all=True to replace all"
                    )
                updated = content.replace(old_string, new_string)
                resolved.write_text(updated, encoding=enc)
                ctx.deps.file_read_mtimes[path_key] = _safe_mtime(resolved)
                return tool_output(
                    f"Patched: {path} ({count} replacement(s))",
                    ctx=ctx,
                    path=str(resolved),
                    replacements=count,
                    strategy="exact",
                )

            # Strategies 2-4: fuzzy matching
            for strategy_name, transform_fn in _FUZZY_STRATEGIES:
                result = _fuzzy_apply(
                    content, old_string, new_string, replace_all, transform_fn, strategy_name
                )
                if isinstance(result, str):
                    return tool_error(result)
                if result is not None:
                    updated, count = result
                    resolved.write_text(updated, encoding=enc)
                    ctx.deps.file_read_mtimes[path_key] = _safe_mtime(resolved)
                    return tool_output(
                        f"Patched: {path} ({count} replacement(s), {strategy_name} strategy)",
                        ctx=ctx,
                        path=str(resolved),
                        replacements=count,
                        strategy=strategy_name,
                    )

            return tool_error(
                f"old_string not found in {path} — verify the text exists in the file"
            )
    except ResourceBusyError:
        return tool_error(f"File {path} is being modified by another tool call — retry next turn")
