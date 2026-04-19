"""Native file system tools: list, read, find, write, patch."""

import asyncio
import difflib
import fnmatch
import re
import shlex
import shutil
from collections.abc import Callable
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools._agent_tool import agent_tool
from co_cli.tools.tool_io import tool_error, tool_output


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


def _is_recursive_pattern(pattern: str) -> bool:
    """Return True when the glob pattern requires recursive traversal."""
    return "**" in pattern or "/" in pattern


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
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
    max_result_size=80_000,
)
async def read_file(
    ctx: RunContext[CoDeps],
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ToolReturn:
    """Read a file's contents for targeted inspection, with optional line range.

    Use for reading known files. Use start_line/end_line to read large files in
    sections — if the response ends with a continuation hint, call read_file again
    with start_line set to the indicated value. If the file is not found, the error
    message includes similar filenames from the same directory to help correct typos.

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
async def grep(
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

    When NOT to use: for file-name discovery — use glob with a pattern instead.

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


@agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, approval=True, retries=1)
async def write_file(
    ctx: RunContext[CoDeps],
    path: str,
    content: str,
) -> ToolReturn:
    """Write content to a new file or intentionally replace all contents of an existing file.

    Use ONLY for creating new files or deliberate full rewrites. Never call this
    after patch on the same file — patch already wrote the change. For targeted
    edits to existing files, use patch instead.

    Creates parent directories as needed. Overwrites the file if it already exists.

    Args:
        path: File path relative to the workspace root.
        content: Text content to write.
    """
    try:
        resolved = _enforce_workspace_boundary(Path(path), ctx.deps.workspace_root)
    except ValueError as e:
        return tool_error(str(e), ctx=ctx)

    from co_cli.tools.resource_lock import ResourceBusyError

    try:
        async with ctx.deps.resource_locks.try_acquire(str(resolved)):
            path_key = str(resolved)
            if (
                path_key in ctx.deps.file_read_mtimes
                and _safe_mtime(resolved) != ctx.deps.file_read_mtimes[path_key]
            ):
                return tool_error("File changed since last read — re-read before writing", ctx=ctx)
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
        return tool_error(
            f"File {path} is being modified by another tool call — retry next turn", ctx=ctx
        )


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


def _build_patch_display(
    content: str,
    updated: str,
    path: str,
    old_string: str,
    new_string: str,
    count: int,
    strategy: str,
    show_diff: bool,
) -> str:
    """Build the display string for a successful patch, optionally prepending a unified diff."""
    old_preview = old_string[:120].replace("\n", "\\n")
    new_preview = new_string[:120].replace("\n", "\\n")
    suffix = (
        f" ({count} replacement(s))"
        if strategy == "exact"
        else f" ({count} replacement(s), {strategy} strategy)"
    )
    body = f"Patched: {path}{suffix}\n  - {old_preview!r}\n  + {new_preview!r}"
    if show_diff:
        return f"{_make_diff_block(content, updated, path)}\n\n{body}"
    return body


def _make_diff_block(content: str, updated: str, path: str) -> str:
    """Return a unified diff string prefixed with [Diff], or (no diff) when unchanged."""
    diff_lines = list(
        difflib.unified_diff(
            content.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    diff_str = "\n".join(diff_lines) if diff_lines else "(no diff)"
    return f"[Diff]\n{diff_str}"


_FUZZY_STRATEGIES: list[tuple[str, Callable[[str], tuple[str, list[int]]]]] = [
    ("line-trimmed", _transform_line_trimmed),
    ("indent-stripped", _transform_indent_stripped),
    ("escape-expanded", _transform_escape_expanded),
]


def _resolve_patch_strategies(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
    show_diff: bool,
    path: str,
) -> tuple[str, str, int, str] | str:
    """Try all four patch strategies. Returns (updated, display, count, strategy) or error string."""
    count = content.count(old_string)
    if count > 0:
        if count > 1 and not replace_all:
            return (
                f"Found {count} occurrences — provide more surrounding context to make "
                "old_string unique, or use replace_all=True to replace all occurrences."
            )
        updated = content.replace(old_string, new_string)
        display = _build_patch_display(
            content, updated, path, old_string, new_string, count, "exact", show_diff
        )
        return updated, display, count, "exact"

    for strategy_name, transform_fn in _FUZZY_STRATEGIES:
        result = _fuzzy_apply(
            content, old_string, new_string, replace_all, transform_fn, strategy_name
        )
        if isinstance(result, str):
            return result
        if result is not None:
            updated, count = result
            display = _build_patch_display(
                content, updated, path, old_string, new_string, count, strategy_name, show_diff
            )
            return updated, display, count, strategy_name

    return f"old_string not found in {path} — verify the text exists in the file"


async def _run_lint_if_python(resolved: Path, display: str) -> str:
    """Append ruff lint warnings to display for .py files; silent on timeout or missing ruff."""
    if resolved.suffix != ".py":
        return display
    try:
        async with asyncio.timeout(5):
            proc = await asyncio.create_subprocess_exec(
                "uv",
                "run",
                "ruff",
                "check",
                str(resolved),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return display + f"\n\n[Auto-Lint Warnings]\n{stdout.decode()}"
    except (TimeoutError, FileNotFoundError):
        pass
    return display


def _check_patch_preconditions(
    resolved: "Path", path: str, path_key: str, ctx: "RunContext[CoDeps]"
) -> str | None:
    """Return an error message if patch write preconditions fail, else None."""
    if path_key not in ctx.deps.file_read_mtimes:
        return f"Read the file with read_file before patching: {path}"
    if path_key in ctx.deps.file_partial_reads:
        return f"Only part of this file was read — call read_file without start_line/end_line before patching: {path}"
    if _safe_mtime(resolved) != ctx.deps.file_read_mtimes[path_key]:
        return "File changed since last read — re-read before writing"
    return None


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
            f"Found {len(matches)} occurrences — provide more surrounding context to make "
            "old_string unique, or use replace_all=True to replace all occurrences."
        )

    result = content
    for orig_start, orig_end in reversed(matches):
        result = result[:orig_start] + new_string + result[orig_end:]
    return result, len(matches)


@agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, approval=True, retries=1)
async def patch(
    ctx: RunContext[CoDeps],
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    show_diff: bool = False,
) -> ToolReturn:
    """Edit a file by replacing old_string with new_string, with fuzzy matching fallback.

    Use for targeted modifications to existing files. You must call read_file
    at least once before patching — this tool will error if the file has not
    been read. old_string must be unique in the file; if not found as-is, re-read
    the file to confirm the text before retrying. Set replace_all=True only when
    every occurrence should be replaced.

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
        show_diff: If True, prepend a unified diff of the change to the display output.
                   Pass True when you need to verify the exact lines changed.
    """
    try:
        resolved = _enforce_workspace_boundary(Path(path), ctx.deps.workspace_root)
    except ValueError as e:
        return tool_error(str(e), ctx=ctx)

    if not resolved.exists():
        return tool_error(f"File not found: {path}", ctx=ctx)

    path_key = str(resolved)
    if err := _check_patch_preconditions(resolved, path, path_key, ctx):
        raise ModelRetry(err)

    from co_cli.tools.resource_lock import ResourceBusyError

    try:
        async with ctx.deps.resource_locks.try_acquire(str(resolved)):
            if resolved.stat().st_size > _MAX_EDIT_BYTES:
                return tool_error(
                    f"File too large to edit in-place ({resolved.stat().st_size // 1024} KB) — use shell tools",
                    ctx=ctx,
                )
            enc = _detect_encoding(resolved)
            content = resolved.read_text(encoding=enc)
            resolution = _resolve_patch_strategies(
                content, old_string, new_string, replace_all, show_diff, path
            )
            if isinstance(resolution, str):
                return tool_error(resolution, ctx=ctx)
            updated, display, count, strategy = resolution
            resolved.write_text(updated, encoding=enc)
            ctx.deps.file_read_mtimes[path_key] = _safe_mtime(resolved)
    except ResourceBusyError:
        return tool_error(
            f"File {path} is being modified by another tool call — retry next turn", ctx=ctx
        )

    display = await _run_lint_if_python(resolved, display)
    return tool_output(
        display,
        ctx=ctx,
        path=str(resolved),
        replacements=count,
        strategy=strategy,
    )
