"""Read-only file system tools: file_read, file_search."""

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
from co_cli.observability.tracing import current_span
from co_cli.proc.env import build_subprocess_env
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.files.fs_guards import (
    detect_encoding,
    enforce_read_boundary,
    is_recursive_pattern,
    safe_mtime,
)
from co_cli.tools.tool_io import READ_MAX_LINES, tool_error, tool_output

_READ_MAX_LINE_CHARS = 2000
_READ_MAX_FILE_BYTES = 500_000


def _compute_read_slice(
    start_line: int | None,
    end_line: int | None,
    total_line_count: int,
) -> tuple[int, int, bool]:
    """Return (lo, hi, is_partial) for a file_read call."""
    if start_line is not None or end_line is not None:
        lo = (start_line - 1) if start_line is not None else 0
        requested_hi = end_line if end_line is not None else total_line_count
        hi = min(requested_hi, lo + READ_MAX_LINES)
        return lo, hi, True
    lo = 0
    hi = min(total_line_count, READ_MAX_LINES)
    return lo, hi, hi < total_line_count


def _build_read_display(
    sliced: list[str],
    base: int,
    total_line_count: int,
    hi: int,
) -> str:
    """Build cat-n numbered display with per-line truncation and optional continuation hint."""
    lines = []
    for i, line in enumerate(sliced):
        raw = line.rstrip("\r\n")
        if len(raw) > _READ_MAX_LINE_CHARS:
            raw = raw[:_READ_MAX_LINE_CHARS] + "...[truncated]"
        lines.append(f"{base + i:>6}\t{raw}\n")
    display = "".join(lines)
    if hi < total_line_count:
        display += (
            f"\n[{total_line_count - hi} more lines — use start_line={hi + 1} to continue reading]"
        )
    return display


def _has_command(cmd: str) -> bool:
    """Return True when a command is available on PATH."""
    return shutil.which(cmd) is not None


def _display_name(path: Path, display_base: Path | None) -> str:
    """Render a hit path for display.

    display_base set (single-root): relative to it, falling back to absolute when
    the path lies outside (byte-identical to the old workspace-relative form).
    display_base None (multi-root): absolute — unambiguous across same-named
    subpaths and round-trips through file_read's read guard (BC-5).
    """
    if display_base is None:
        return str(path)
    try:
        return str(path.relative_to(display_base))
    except ValueError:
        return str(path)


def _split_path_glob(path: str) -> tuple[str, str]:
    """Split a glob-or-directory path into (literal_prefix, glob).

    The leading run of components without glob metacharacters becomes the
    literal directory prefix (search root); the remainder is the glob. A path
    with no glob component is a plain directory, matched recursively ("**/*").

    Examples:
      "**/*.py"          -> (".", "**/*.py")
      "co_cli/**/*.py"   -> ("co_cli", "**/*.py")
      "src/*.py"         -> ("src", "*.py")
      "*"                -> (".", "*")
      "co_cli"           -> ("co_cli", "**/*")
    """
    parts = Path(path).parts
    glob_index = len(parts)
    for index, part in enumerate(parts):
        if any(ch in part for ch in "*?["):
            glob_index = index
            break
    literal = parts[:glob_index]
    glob = "/".join(parts[glob_index:]) if glob_index < len(parts) else "**/*"
    prefix = str(Path(*literal)) if literal else "."
    return prefix, glob


async def _glob_python(
    resolved: Path, display_base: Path | None, pattern: str, max_entries: int
) -> tuple[list[dict[str, str]], bool]:
    entries = []
    truncated = False
    if is_recursive_pattern(pattern):
        raw = sorted(resolved.glob(pattern), key=safe_mtime, reverse=True)
        for entry in raw:
            kind = "dir" if entry.is_dir() else "file"
            entries.append({"name": _display_name(entry, display_base), "type": kind})
            if len(entries) >= max_entries:
                truncated = True
                break
    else:
        for entry in sorted(resolved.iterdir()):
            if not fnmatch.fnmatch(entry.name, pattern):
                continue
            kind = "dir" if entry.is_dir() else "file"
            name = entry.name if display_base is not None else str(entry)
            entries.append({"name": name, "type": kind})
            if len(entries) >= max_entries:
                truncated = True
                break
    return entries, truncated


async def _glob_ripgrep(
    resolved: Path, display_base: Path | None, pattern: str, max_entries: int
) -> tuple[list[dict[str, str]], bool] | None:
    # Run rg from `resolved` with no dir arg: path-prefix globs like src/**/*.py only work
    # correctly when rg resolves paths relative to its cwd, not against an absolute dir arg.
    # --no-config: ignore user's ~/.config/ripgrep/ripgrep.toml to prevent config interference.
    # --sortr=modified: rg-native mtime sort (rg 13+); avoids per-file stat calls.
    #   If the flag is unknown (rg <13) rg exits non-zero → retry without sort.
    base_args = ["rg", "--files", "--null", "--hidden", "--no-config", "-g", pattern]

    async def _run(args: list[str]) -> bytes | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=resolved,
                env=build_subprocess_env(),
            )
            stdout, _ = await proc.communicate()
        except Exception:
            return None
        return stdout if proc.returncode in (0, 1) else None

    stdout = await _run([*base_args, "--sortr=modified"])
    if stdout is None:
        # --sortr unknown (rg <13) or fatal error — retry without sort
        stdout = await _run(base_args)
        if stdout is None:
            return None
    if not stdout:
        return [], False
    # Paths are relative to `resolved`; reconstruct absolute before relativizing to workspace.
    raw_paths = [resolved / p.decode("utf-8", errors="replace") for p in stdout.split(b"\0") if p]
    truncated = len(raw_paths) > max_entries
    entries = []
    for path in raw_paths[:max_entries]:
        entries.append({"name": _display_name(path, display_base), "type": "file"})
    return entries, truncated


def _parse_grep_content_output(
    lines: list[str], display_base: Path | None
) -> tuple[list[str], int]:
    """Parse ripgrep content-mode output into the tool's display format."""
    match_re = re.compile(r"^([A-Za-z]:)?(.*?):(\d+):(.*)$")
    total_match_count = 0
    all_output: list[str] = []
    for line in lines:
        if not line:
            continue
        match = match_re.match(line)
        if match:
            path_str = (match.group(1) or "") + match.group(2)
            rel_path = _display_name(Path(path_str), display_base)
            all_output.append(f"{rel_path}:{match.group(3)}: {match.group(4)}")
            total_match_count += 1
    return all_output, total_match_count


def _build_grep_shell_command(
    resolved: Path,
    pattern: str,
    glob_pat: str,
    case_insensitive: bool,
    output_mode: str,
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
    if glob_pat:
        cmd_parts.extend(["--glob", shlex.quote(glob_pat)])
    if output_mode == "files_only":
        cmd_parts.append("-l")
    cmd_parts.append(shlex.quote(pattern))
    cmd_parts.append(shlex.quote(str(resolved)))
    return " ".join(cmd_parts)


def _parse_grep_shell_output(
    lines: list[str], display_base: Path | None, output_mode: str
) -> tuple[list[str], int]:
    """Parse shell grep output according to the requested display mode."""
    if output_mode == "files_only":
        all_output = [_display_name(Path(line), display_base) for line in lines if line]
        return all_output, len(all_output)
    return _parse_grep_content_output(lines, display_base)


async def _grep_shell(
    resolved: Path,
    display_base: Path | None,
    pattern: str,
    glob_pat: str,
    case_insensitive: bool,
    output_mode: str,
    head_limit: int,
    offset: int,
) -> tuple[list[str], int, bool] | str | None:
    cmd = _build_grep_shell_command(resolved, pattern, glob_pat, case_insensitive, output_mode)

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=build_subprocess_env(),
        )
        stdout, _stderr = await proc.communicate()
        if proc.returncode not in (0, 1):
            return None
        lines = stdout.decode().strip().split("\n")
        if not lines or not lines[0]:
            return [], 0, False
        all_output, total_match_count = _parse_grep_shell_output(lines, display_base, output_mode)

        paginated = (
            all_output[offset : offset + head_limit] if head_limit > 0 else all_output[offset:]
        )
        truncated = len(all_output) - offset > len(paginated)
        return paginated, total_match_count, truncated
    except Exception:
        return None


async def _grep_python(
    search_root: Path,
    display_base: Path | None,
    pattern: str,
    glob_pat: str,
    case_insensitive: bool,
    output_mode: str,
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
        rel_path = _display_name(file_path, display_base)
        file_lines = text.splitlines()
        match_indices = [idx for idx, line in enumerate(file_lines) if compiled.search(line)]
        if not match_indices:
            continue
        total_match_count += len(match_indices)
        all_output.extend(
            _grep_format_file_matches(rel_path, file_lines, match_indices, output_mode)
        )
    paginated = all_output[offset : offset + head_limit] if head_limit > 0 else all_output[offset:]
    truncated = len(all_output) - offset > len(paginated)
    return paginated, total_match_count, truncated


def _grep_format_file_matches(
    rel_path: str,
    file_lines: list[str],
    match_indices: list[int],
    output_mode: str,
) -> list[str]:
    """Format grep matches for a single file according to output_mode."""
    if output_mode == "files_only":
        return [rel_path]
    return [f"{rel_path}:{idx + 1}: {file_lines[idx]}" for idx in match_indices]


async def _glob_roots(
    search_targets: list[Path],
    display_base: Path | None,
    glob: str,
    fetch_cap: int,
) -> tuple[list[dict[str, str]], bool]:
    """Run the single-root glob machinery across each target root and concat entries.

    Cross-root order is per-root-grouped (each root pre-sorted by mtime); there is no
    true global mtime sort — glob entries carry no mtime and re-stat'ing every path is
    not worth it. `truncated` is the OR of the per-root caps; the caller applies the
    global offset/limit slice on the concatenated list.
    """
    entries: list[dict[str, str]] = []
    truncated = False
    for target in search_targets:
        if is_recursive_pattern(glob) and _has_command("rg"):
            rg_result = await _glob_ripgrep(target, display_base, glob, fetch_cap)
            t_entries, t_trunc = (
                rg_result
                if rg_result is not None
                else await _glob_python(target, display_base, glob, fetch_cap)
            )
        else:
            t_entries, t_trunc = await _glob_python(target, display_base, glob, fetch_cap)
        entries.extend(t_entries)
        truncated = truncated or t_trunc
    return entries, truncated


async def _grep_roots(
    search_targets: list[Path],
    display_base: Path | None,
    content: str,
    glob: str,
    case_insensitive: bool,
    output_mode: str,
    fetch_cap: int,
) -> tuple[list[str], int, bool] | str:
    """Run the single-root grep machinery across each target root and merge results.

    Each root is called with offset 0 and head_limit=fetch_cap so its internal
    pagination is neutralized; the caller slices the merged list globally.
    total_match_count sums across roots. Returns an error string on bad regex.
    """
    merged: list[str] = []
    total_match_count = 0
    truncated = False
    for target in search_targets:
        result = None
        if _has_command("rg"):
            result = await _grep_shell(
                target, display_base, content, glob, case_insensitive, output_mode, fetch_cap, 0
            )
        if result is None:
            result = await _grep_python(
                target, display_base, content, glob, case_insensitive, output_mode, fetch_cap, 0
            )
        if isinstance(result, str):
            return result
        t_paginated, t_total, t_trunc = result
        merged.extend(t_paginated)
        total_match_count += t_total
        truncated = truncated or t_trunc
    return merged, total_match_count, truncated


@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_concurrent_safe=True,
    spill_threshold_chars=math.inf,
)
async def file_read(
    ctx: RunContext[CoDeps],
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ToolReturn:
    """Read a file's contents for targeted inspection, with optional line range.

    Use for reading known files. To read a large file in sections, pass
    start_line/end_line; if the response ends with a continuation hint, call
    again with start_line set to the indicated value.

    When NOT to use: when the file location is unknown — use file_search first to locate it.

    Args:
        path: Path relative to the workspace root, or an absolute path under a
            configured file-search root (the form file_search prints when more
            than one root is active).
        start_line: First line to include, 1-indexed inclusive. Default None →
            read from the top, up to 500 lines.
        end_line: Last line to include, 1-indexed inclusive. Default None → read
            through line 500 or end of file.
    """
    try:
        refetch_attempt = Path(path).resolve().is_relative_to(ctx.deps.tool_results_dir.resolve())
    except (OSError, ValueError):
        refetch_attempt = False
    current_span().set_attribute("co.tool.spill_refetch_attempt", refetch_attempt)
    # tool_results_dir is an allowed read root so the model can re-fetch its own
    # spilled tool results (spill_if_oversized writes there and instructs file_read
    # to read the path back); it is never a file_search root.
    read_roots = [*ctx.deps.file_search_roots, ctx.deps.tool_results_dir]
    try:
        resolved, _root = enforce_read_boundary(Path(path), read_roots)
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

    st = resolved.stat()
    # Block full-file reads on large files to protect context budget.
    # Explicit ranges still proceed; the line cap limits their output size.
    if start_line is None and end_line is None and st.st_size > _READ_MAX_FILE_BYTES:
        return tool_error(
            f"File too large for full-file read ({st.st_size // 1024} KB). "
            "Use start_line/end_line to keep this result within context budget.",
            ctx=ctx,
        )

    try:
        enc = detect_encoding(resolved)
        content = resolved.read_text(encoding=enc)
    except UnicodeDecodeError:
        return tool_error(f"Binary file — cannot display as text: {path}", ctx=ctx)

    path_key = str(resolved)
    all_lines = content.splitlines(keepends=True)
    total_line_count = len(all_lines)

    lo, hi, is_partial = _compute_read_slice(start_line, end_line, total_line_count)
    sliced = all_lines[lo:hi]

    ctx.deps.file_tracker.record_read(path_key, st.st_mtime, partial=is_partial)

    base = start_line if start_line is not None else 1
    display = _build_read_display(sliced, base, total_line_count, hi)

    return tool_output(
        display,
        ctx=ctx,
        path=str(resolved),
        lines=total_line_count,
    )


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=True)
async def file_search(
    ctx: RunContext[CoDeps],
    path: str = "**/*",
    content: str | None = None,
    case_insensitive: bool = False,
    files_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> ToolReturn:
    """Find files by path glob, or regex-search inside them. Replaces grep/rg/find/ls.

    Two operations, chosen by whether `content` is given:
      - content omitted -> list the files matching `path` (like find/ls).
      - content given   -> regex-search inside the files matching `path` (like grep).

    `path` always selects WHICH files; `content` is WHAT to find inside them.

    Examples:
      file_search(path="**/*.py")                     -> list all Python files
      file_search(path="co_cli/")                     -> list files under co_cli/
      file_search(content="TODO")                     -> grep "TODO" across all files
      file_search(path="**/*.py", content="def main") -> grep within Python files only
      file_search(content="auth", files_only=True)    -> which files contain "auth"

    Args:
        path: Glob selecting which files (e.g. "**/*.py", "src/*.ts", "*config*"),
            or a directory to search under. Default "**/*" = every file under the
            active file-search root(s), recursively. Use "*" for a flat listing of
            one directory. Hits print relative to the workspace when a single root
            is active, and as absolute paths when more than one root is configured
            (feed an absolute hit straight back to file_read).
        content: Python regex to search for inside the matched files. Default None;
            when omitted, the tool returns the list of matching files instead of
            searching their contents. Binary files are skipped.
        case_insensitive: Match `content` regardless of case. Default False
            (case-sensitive). Only affects content search.
        files_only: When True and `content` is set, return only the paths of files
            that contain a match, not the matching lines. Default False (return the
            matching lines). Use it for "which files contain X" questions.
        limit: Maximum results returned — file entries, or matching lines. Default 50.
            0 means unlimited; with more than one root configured an unlimited
            content search is capped per root to bound a cross-root scan. Pair with
            `offset` to page through more.
        offset: Number of results to skip before returning, for pagination. Default 0
            (start from the first result). Use together with `limit`.
    """
    roots = ctx.deps.file_search_roots
    multi_root = len(roots) > 1
    # Single root: hits render relative to it (byte-identical to today). Multi-root:
    # absolute, so they are unambiguous and round-trip through file_read (BC-5).
    display_base = None if multi_root else roots[0]
    prefix, glob = _split_path_glob(path)

    # An explicit literal prefix binds to its one containing root (single-root search,
    # as today); a bare glob (prefix ".") fans across every configured root.
    if prefix != ".":
        try:
            search_root, _root = enforce_read_boundary(Path(prefix), roots)
        except ValueError as e:
            return tool_error(str(e), ctx=ctx)
        if not search_root.is_dir():
            return tool_error(f"Not a directory: {prefix}", ctx=ctx)
        search_targets = [search_root]
    else:
        search_targets = [r for r in roots if r.is_dir()]

    if content is None:
        # Glob path is already 200-capped for the unlimited (limit<=0) case today.
        fetch_cap = (offset + limit) if limit > 0 else 200
        entries, truncated = await _glob_roots(search_targets, display_base, glob, fetch_cap)
        page = entries[offset:] if limit <= 0 else entries[offset : offset + limit]
        truncated = truncated or (len(entries) - offset > len(page))
        file_lines = [f"[{e['type']}] {e['name']}" for e in page]
        if truncated:
            file_lines.append("(truncated — narrow `path` or page with `offset`)")
        display = "\n".join(file_lines) if file_lines else "(empty)"
        return tool_output(
            display,
            ctx=ctx,
            path=path,
            count=len(page),
            truncated=truncated,
        )

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        re.compile(content, flags)
    except re.error:
        return tool_error(f"Invalid regex: {content}", ctx=ctx)

    output_mode = "files_only" if files_only else "content"
    # Per-root pagination is neutralized (offset=0 to each helper) so the merged list
    # can be sliced globally. fetch_cap bounds each root: a positive limit pages
    # exactly (offset+limit covers the global page); the unlimited (limit<=0) case
    # stays truly unbounded for a single root (byte-identical to today) but takes a
    # 200-row ceiling per root once more than one root is searched — never fan an
    # uncapped content scan across every root (incl. a vault).
    if limit > 0:
        fetch_cap = offset + limit
    elif multi_root:
        fetch_cap = 200
    else:
        fetch_cap = 0
    if multi_root and limit <= 0:
        current_span().set_attribute("co.tool.file_search.multiroot_grep_capped", True)

    result = await _grep_roots(
        search_targets, display_base, content, glob, case_insensitive, output_mode, fetch_cap
    )
    if isinstance(result, str):
        return tool_error(result, ctx=ctx)
    merged, total_match_count, truncated = result
    page = merged[offset:] if limit <= 0 else merged[offset : offset + limit]
    truncated = truncated or (len(merged) - offset > len(page))
    display = "\n".join(page) if page else "(no matches)"

    return tool_output(
        display,
        ctx=ctx,
        content=content,
        count=total_match_count,
        mode=output_mode,
        truncated=truncated,
    )
