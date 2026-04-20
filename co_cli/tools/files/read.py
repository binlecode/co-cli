"""Read-only file system tools: file_glob, file_read, file_grep."""

import asyncio
import difflib
import fnmatch
import math
import re
import shlex
import shutil
from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.files.helpers import (
    _detect_encoding,
    _enforce_workspace_boundary,
    _is_recursive_pattern,
    _safe_mtime,
)
from co_cli.tools.tool_io import tool_error, tool_output


def _has_command(cmd: str) -> bool:
    """Return True when a command is available on PATH."""
    return shutil.which(cmd) is not None


async def _glob_python(
    resolved: Path, workspace_root: Path, pattern: str, max_entries: int
) -> tuple[list[dict[str, str]], bool]:
    entries = []
    truncated = False
    if _is_recursive_pattern(pattern):
        raw = sorted(resolved.glob(pattern), key=_safe_mtime, reverse=True)
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
        for entry in sorted(resolved.iterdir()):
            if not fnmatch.fnmatch(entry.name, pattern):
                continue
            kind = "dir" if entry.is_dir() else "file"
            entries.append({"name": entry.name, "type": kind})
            if len(entries) >= max_entries:
                truncated = True
                break
    return entries, truncated


def _relativize_output_path(path_str: str, workspace_root: Path) -> str:
    """Return a workspace-relative path when possible."""
    try:
        return str(Path(path_str).relative_to(workspace_root))
    except ValueError:
        return path_str


def _parse_grep_count_output(lines: list[str], workspace_root: Path) -> tuple[list[str], int]:
    """Parse ripgrep count-mode output into display lines and total match count."""
    total_match_count = 0
    all_output: list[str] = []
    for line in lines:
        if ":" not in line:
            continue
        path_str, count_str = line.rsplit(":", 1)
        try:
            count = int(count_str)
        except ValueError:
            continue
        total_match_count += count
        rel_path = _relativize_output_path(path_str, workspace_root)
        all_output.append(f"{rel_path}: {count}")
    return all_output, total_match_count


def _parse_grep_content_output(lines: list[str], workspace_root: Path) -> tuple[list[str], int]:
    """Parse ripgrep content-mode output into the tool's legacy display format."""
    match_re = re.compile(r"^([A-Za-z]:)?(.*?):(\d+):(.*)$")
    context_re = re.compile(r"^([A-Za-z]:)?(.*?)-(\d+)-(.*)$")
    total_match_count = 0
    all_output: list[str] = []
    for line in lines:
        if not line or line == "--":
            all_output.append("--")
            continue
        match = match_re.match(line)
        if match:
            path_str = (match.group(1) or "") + match.group(2)
            rel_path = _relativize_output_path(path_str, workspace_root)
            all_output.append(f"{rel_path}:{match.group(3)}: {match.group(4)}")
            total_match_count += 1
            continue
        context_match = context_re.match(line)
        if context_match:
            path_str = (context_match.group(1) or "") + context_match.group(2)
            rel_path = _relativize_output_path(path_str, workspace_root)
            all_output.append(f"{rel_path}:{context_match.group(3)}- {context_match.group(4)}")

    filtered_output: list[str] = []
    for entry in all_output:
        if entry == "--" and (not filtered_output or filtered_output[-1] == "--"):
            continue
        filtered_output.append(entry)
    if filtered_output and filtered_output[-1] == "--":
        filtered_output.pop()
    return filtered_output, total_match_count


def _build_grep_shell_command(
    resolved: Path,
    pattern: str,
    glob_pat: str,
    case_insensitive: bool,
    output_mode: str,
    context_lines: int,
) -> str:
    """Build a ripgrep command line that preserves the tool's search surface."""
    cmd_parts = [
        "rg",
        "--line-number",
        "--no-heading",
        "--with-filename",
        "--hidden",
        "--no-ignore",
    ]
    if case_insensitive:
        cmd_parts.append("-i")
    if context_lines > 0:
        cmd_parts.extend(["-C", str(context_lines)])
    if glob_pat:
        cmd_parts.extend(["--glob", shlex.quote(glob_pat)])
    if output_mode == "files_with_matches":
        cmd_parts.append("-l")
    elif output_mode == "count":
        cmd_parts.append("-c")
    cmd_parts.append(shlex.quote(pattern))
    cmd_parts.append(shlex.quote(str(resolved)))
    return " ".join(cmd_parts)


def _parse_grep_shell_output(
    lines: list[str], workspace_root: Path, output_mode: str
) -> tuple[list[str], int]:
    """Parse shell grep output according to the requested display mode."""
    if output_mode == "files_with_matches":
        all_output = [_relativize_output_path(line, workspace_root) for line in lines if line]
        return all_output, len(all_output)
    if output_mode == "count":
        return _parse_grep_count_output(lines, workspace_root)
    return _parse_grep_content_output(lines, workspace_root)


async def _grep_shell(
    resolved: Path,
    workspace_root: Path,
    pattern: str,
    glob_pat: str,
    case_insensitive: bool,
    output_mode: str,
    context_lines: int,
    head_limit: int,
    offset: int,
) -> tuple[list[str], int, bool] | str | None:
    cmd = _build_grep_shell_command(
        resolved, pattern, glob_pat, case_insensitive, output_mode, context_lines
    )

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await proc.communicate()
        if proc.returncode not in (0, 1):
            return None
        lines = stdout.decode().strip().split("\n")
        if not lines or not lines[0]:
            return [], 0, False
        all_output, total_match_count = _parse_grep_shell_output(
            lines, workspace_root, output_mode
        )

        paginated = (
            all_output[offset : offset + head_limit] if head_limit > 0 else all_output[offset:]
        )
        truncated = len(all_output) - offset > len(paginated)
        return paginated, total_match_count, truncated
    except Exception:
        return None


async def _grep_python(
    search_root: Path,
    workspace_root: Path,
    pattern: str,
    glob_pat: str,
    case_insensitive: bool,
    output_mode: str,
    context_lines: int,
    head_limit: int,
    offset: int,
) -> tuple[list[str], int, bool] | str:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error:
        return f"Invalid regex: {pattern}"
    all_output: list[str] = []
    total_match_count = 0
    for file_path in search_root.glob(glob_pat):
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel_path = str(file_path.relative_to(workspace_root))
        file_lines = text.splitlines()
        match_indices = [idx for idx, line in enumerate(file_lines) if compiled.search(line)]
        if not match_indices:
            continue
        total_match_count += len(match_indices)
        all_output.extend(
            _grep_format_file_matches(
                rel_path, file_lines, match_indices, output_mode, context_lines
            )
        )
    paginated = all_output[offset : offset + head_limit] if head_limit > 0 else all_output[offset:]
    truncated = len(all_output) - offset > len(paginated)
    return paginated, total_match_count, truncated


def _grep_context_output(
    rel_path: str, file_lines: list[str], match_indices: list[int], context: int
) -> list[str]:
    """Build grep-style context output for matched lines in one file.

    Merges overlapping context windows and inserts '--' between disjoint groups.
    Match lines use ':' separator; context lines use '-' separator.
    """
    ranges: list[tuple[int, int]] = []
    for idx in match_indices:
        lo = max(0, idx - context)
        hi = min(len(file_lines) - 1, idx + context)
        if ranges and lo <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], hi)
        else:
            ranges.append((lo, hi))

    match_set = set(match_indices)
    output: list[str] = []
    for group_idx, (lo, hi) in enumerate(ranges):
        if group_idx > 0:
            output.append("--")
        for line_idx in range(lo, hi + 1):
            sep = ":" if line_idx in match_set else "-"
            output.append(f"{rel_path}:{line_idx + 1}{sep} {file_lines[line_idx]}")
    return output


def _grep_format_file_matches(
    rel_path: str,
    file_lines: list[str],
    match_indices: list[int],
    output_mode: str,
    context_lines: int,
) -> list[str]:
    """Format grep matches for a single file according to output_mode."""
    if output_mode == "content":
        if context_lines > 0:
            return _grep_context_output(rel_path, file_lines, match_indices, context_lines)
        return [f"{rel_path}:{idx + 1}: {file_lines[idx]}" for idx in match_indices]
    if output_mode == "files_with_matches":
        return [rel_path]
    # count mode
    return [f"{rel_path}: {len(match_indices)}"]


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def file_glob(
    ctx: RunContext[CoDeps],
    path: str = ".",
    pattern: str = "*",
    max_entries: int = 200,
) -> ToolReturn:
    """List directory contents or find files by name pattern (glob).

    Use for file-name and path discovery — when you need to know what files
    exist or find files by extension/name pattern. Use "**/*.ext" for recursive
    search by name (results sorted by modification time, newest first).

    When NOT to use: for content search inside files — use file_grep instead.

    Args:
        path: Directory path relative to the workspace root (default: current directory).
        pattern: Glob pattern to filter entries (default: "*" matches all).
                 Use "**/*.ext" for recursive file search by name.
        max_entries: Maximum number of entries to return (default: 200).
    """
    try:
        resolved = _enforce_workspace_boundary(Path(path), ctx.deps.workspace_root)
    except ValueError as e:
        return tool_error(str(e), ctx=ctx)

    if not resolved.exists():
        return tool_error(f"Path not found: {path}", ctx=ctx)

    if not resolved.is_dir():
        return tool_error(f"Not a directory: {path}", ctx=ctx)

    workspace_root = ctx.deps.workspace_root
    truncated = False

    entries, truncated = await _glob_python(resolved, workspace_root, pattern, max_entries)

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


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
    max_result_size=math.inf,
)
async def file_read(
    ctx: RunContext[CoDeps],
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ToolReturn:
    """Read a file's contents for targeted inspection, with optional line range.

    Use for reading known files. Use start_line/end_line to read large files in
    sections — if the response ends with a continuation hint, call file_read again
    with start_line set to the indicated value. If the file is not found, the error
    message includes similar filenames from the same directory to help correct typos.

    When NOT to use: when the file location is unknown — use file_glob or
    file_grep first to locate the file.

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
        return tool_error(str(e), ctx=ctx)

    if not resolved.exists():
        error_msg = f"File not found: {path}"
        if resolved.parent.exists():
            names = [p.name for p in resolved.parent.iterdir()]
            matches = difflib.get_close_matches(resolved.name, names, n=3, cutoff=0.6)
            if matches:
                error_msg += f"\nSimilar files: {', '.join(matches)}"
        return tool_error(error_msg, ctx=ctx)

    if resolved.is_dir():
        return tool_error(f"Path is a directory: {path}", ctx=ctx)

    try:
        enc = _detect_encoding(resolved)
        content = resolved.read_text(encoding=enc)
    except UnicodeDecodeError:
        return tool_error(f"Binary file — cannot display as text: {path}", ctx=ctx)

    path_key = str(resolved)
    ctx.deps.file_read_mtimes[path_key] = resolved.stat().st_mtime
    is_partial = start_line is not None or end_line is not None
    if is_partial:
        ctx.deps.file_partial_reads.add(path_key)
    else:
        ctx.deps.file_partial_reads.discard(path_key)
    all_lines = content.splitlines(keepends=True)
    total_line_count = len(all_lines)

    if start_line is not None or end_line is not None:
        lo = (start_line - 1) if start_line is not None else 0
        hi = end_line if end_line is not None else total_line_count
        sliced = all_lines[lo:hi]
    else:
        hi = total_line_count
        sliced = all_lines

    # cat -n style: right-justified 6-char line number + tab
    base = start_line if start_line is not None else 1
    display = "".join(f"{base + i:>6}\t{line}" for i, line in enumerate(sliced))

    if end_line is not None and total_line_count > hi:
        display += (
            f"\n[{total_line_count - hi} more lines — use start_line={hi + 1} to continue reading]"
        )

    return tool_output(
        display,
        ctx=ctx,
        path=str(resolved),
        lines=total_line_count,
    )


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
async def file_grep(
    ctx: RunContext[CoDeps],
    pattern: str,
    path: str = ".",
    glob: str = "**/*",
    case_insensitive: bool = False,
    output_mode: str = "content",
    context_lines: int = 0,
    head_limit: int = 250,
    offset: int = 0,
) -> ToolReturn:
    """Search file contents by regex pattern across the workspace or a subdirectory.

    Use for content search — finding text, symbols, or patterns inside files.
    Prefer this over shell grep/rg for workspace content search.

    When NOT to use: for file-name discovery — use file_glob with a pattern instead.

    Skips binary files.

    output_mode controls what is returned:
      "content"           — matching lines with file:line_no: text (default)
      "files_with_matches" — only file paths that contain at least one match
      "count"             — file path and match count per file

    Args:
        pattern: Regular expression to search for.
        path: Directory to search within, relative to the workspace root (default: ".").
        glob: Glob pattern to filter which files are searched (default: "**/*").
        case_insensitive: If True, match regardless of case (default: False).
        output_mode: One of "content", "files_with_matches", "count" (default: "content").
        context_lines: Lines of context before and after each match in content mode (default: 0).
        head_limit: Maximum output lines/entries to return; 0 means unlimited (default: 250).
        offset: Skip the first N output lines/entries for pagination (default: 0).
    """
    _VALID_MODES = {"content", "files_with_matches", "count"}
    if output_mode not in _VALID_MODES:
        return tool_error(
            f"Invalid output_mode {output_mode!r} — use 'content', 'files_with_matches', or 'count'",
            ctx=ctx,
        )

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        re.compile(pattern, flags)
    except re.error:
        return tool_error(f"Invalid regex: {pattern}", ctx=ctx)

    workspace_root = ctx.deps.workspace_root
    try:
        search_root = _enforce_workspace_boundary(Path(path), workspace_root)
    except ValueError as e:
        return tool_error(str(e), ctx=ctx)
    if not search_root.is_dir():
        return tool_error(f"Not a directory: {path}", ctx=ctx)

    result = None
    if _has_command("rg"):
        result = await _grep_shell(
            search_root,
            workspace_root,
            pattern,
            glob,
            case_insensitive,
            output_mode,
            context_lines,
            head_limit,
            offset,
        )

    if result is None:
        result = await _grep_python(
            search_root,
            workspace_root,
            pattern,
            glob,
            case_insensitive,
            output_mode,
            context_lines,
            head_limit,
            offset,
        )

    if isinstance(result, str):
        return tool_error(result, ctx=ctx)

    paginated, total_match_count, truncated = result
    display = "\n".join(paginated) if paginated else "(no matches)"

    return tool_output(
        display,
        ctx=ctx,
        pattern=pattern,
        count=total_match_count,
        mode=output_mode,
        truncated=truncated,
    )
