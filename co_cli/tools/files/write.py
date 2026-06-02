"""Write file system tools: file_write, file_patch."""

import asyncio
import difflib
from collections.abc import Callable
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.files.fs_guards import (
    detect_encoding,
    enforce_write_boundary,
    safe_mtime,
)
from co_cli.tools.shell_env import build_subprocess_env
from co_cli.tools.tool_io import tool_error, tool_output

_MAX_EDIT_BYTES = 10 * 1024 * 1024  # 10 MB hard block for patch


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
                env=build_subprocess_env(),
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
    if not ctx.deps.file_tracker.is_read(path_key):
        return f"Read the file with file_read before patching: {path}"
    if ctx.deps.file_tracker.is_partial(path_key):
        return f"Only part of this file was read — call file_read without start_line/end_line before patching: {path}"
    if ctx.deps.file_tracker.is_stale(path_key, safe_mtime(resolved)):
        return "File changed since last read — re-read before writing"
    return None


# Approval modal cannot be queued safely for two concurrent writes to the same path.
@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS, approval=True, retries=1, is_concurrent_safe=False
)
async def file_write(
    ctx: RunContext[CoDeps],
    path: str,
    content: str,
) -> ToolReturn:
    """Write content to a new file or intentionally replace all contents of an existing file.

    Use ONLY for creating new files or deliberate full rewrites. Never call this
    after patch on the same file — patch already wrote the change. For targeted
    edits to existing files, use file_patch instead.

    Creates parent directories as needed. Overwrites the file if it already exists.

    Direct write, follows symlinks; not atomic. For internal atomic writes,
    use `co_cli.fileio.atomic.atomic_write_text`.

    Args:
        path: File path relative to the workspace root.
        content: Text content to write.
    """
    try:
        resolved = enforce_write_boundary(Path(path), ctx.deps.workspace_dir)
    except ValueError as e:
        return tool_error(str(e), ctx=ctx)

    from co_cli.tools.resource_lock import ResourceBusyError

    try:
        async with ctx.deps.resource_locks.try_acquire(str(resolved)):
            path_key = str(resolved)
            if ctx.deps.file_tracker.is_read_and_stale(path_key, safe_mtime(resolved)):
                return tool_error("File changed since last read — re-read before writing", ctx=ctx)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            byte_count = len(content.encode("utf-8"))
            ctx.deps.file_tracker.update_mtime(path_key, safe_mtime(resolved))
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


# Approval modal cannot be queued safely for concurrent patches; read-modify-write demands no interleaving.
@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS, approval=True, retries=1, is_concurrent_safe=False
)
async def file_patch(
    ctx: RunContext[CoDeps],
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    show_diff: bool = False,
) -> ToolReturn:
    """Make a targeted find-and-replace edit in a single existing file.

    Use this instead of sed/awk — never shell redirection — for editing. Requires
    file_read on the file first. Tries four matching strategies (exact, line-trimmed,
    indent-stripped, escape-expanded) so minor whitespace differences won't break the
    match. Returns the replacement count and strategy.

    When NOT to use: creating a new file or a full rewrite — use file_write. To delete
    a whole file, use shell_exec (rm).

    Args:
        path: File path relative to the workspace root.
        old_string: Exact text to find. Must be unique in the file unless replace_all=True;
            include surrounding context lines to make it unique.
        new_string: Replacement text, applied verbatim. Pass "" to delete the matched text.
        replace_all: Replace every occurrence instead of requiring a unique match
            (default False = require exactly one match).
        show_diff: Prepend a unified diff of the change to the output (default False).
    """
    try:
        resolved = enforce_write_boundary(Path(path), ctx.deps.workspace_dir)
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
            enc = detect_encoding(resolved)
            content = resolved.read_text(encoding=enc)
            resolution = _resolve_patch_strategies(
                content, old_string, new_string, replace_all, show_diff, path
            )
            if isinstance(resolution, str):
                return tool_error(resolution, ctx=ctx)
            updated, display, count, strategy = resolution
            resolved.write_text(updated, encoding=enc)
            ctx.deps.file_tracker.update_mtime(path_key, safe_mtime(resolved))
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
