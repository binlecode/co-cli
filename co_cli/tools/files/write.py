"""Write file system tools: file_write, file_patch."""

import asyncio
import difflib
from collections.abc import Callable
from pathlib import Path

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.files._v4a import (
    Hunk,
    OperationType,
    PatchOperation,
    parse_v4a_patch,
)
from co_cli.tools.files.helpers import (
    _detect_encoding,
    _enforce_workspace_boundary,
    _safe_mtime,
)
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
        return f"Read the file with file_read before patching: {path}"
    if path_key in ctx.deps.file_partial_reads:
        return f"Only part of this file was read — call file_read without start_line/end_line before patching: {path}"
    if _safe_mtime(resolved) != ctx.deps.file_read_mtimes[path_key]:
        return "File changed since last read — re-read before writing"
    return None


# ── V4A multi-file patch apply ─────────────────────────────────────────────────
# Parser lives in _v4a.py (ported from hermes-agent/tools/patch_parser.py).
# The apply side uses co-cli conventions: resource locks, _resolve_patch_strategies,
# stale-read block, and _run_lint_if_python.

# pending-write tuple: (resolved, content_or_None, enc, rel_path, display_chunk, kind)
_PendingWrite = tuple[Path, str | None, str, str, str, OperationType]


def _insert_addition_hunk(current: str, hunk: Hunk, insert_text: str) -> str:
    """Insert addition-only hunk content after context hint, or append to end."""
    if hunk.context_hint and hunk.context_hint in current:
        hint_pos = current.find(hunk.context_hint)
        eol = current.find("\n", hint_pos)
        if eol != -1:
            return current[: eol + 1] + insert_text + "\n" + current[eol + 1 :]
        return current + "\n" + insert_text
    return current.rstrip("\n") + "\n" + insert_text + "\n"


def _compute_v4a_update(
    op: PatchOperation,
    resolved: Path,
    ctx: "RunContext[CoDeps]",
) -> "_PendingWrite | str":
    """Compute new content for a V4A UPDATE op. Returns pending tuple or error string."""
    rel_path = op.file_path
    path_key = str(resolved)

    if not resolved.exists():
        return f"File not found: {rel_path}"
    if err := _check_patch_preconditions(resolved, rel_path, path_key, ctx):
        return err
    if resolved.stat().st_size > _MAX_EDIT_BYTES:
        return f"File too large to patch: {rel_path}"

    enc = _detect_encoding(resolved)
    original = resolved.read_text(encoding=enc)
    current = original

    for hunk in op.hunks:
        old_str = "\n".join(line.content for line in hunk.lines if line.prefix in (" ", "-"))
        new_str = "\n".join(line.content for line in hunk.lines if line.prefix in (" ", "+"))
        if not old_str:
            current = _insert_addition_hunk(current, hunk, new_str)
            continue
        resolution = _resolve_patch_strategies(current, old_str, new_str, False, False, rel_path)
        if isinstance(resolution, str):
            return f"{rel_path}: {resolution}"
        current, _, _, _ = resolution

    diff = _make_diff_block(original, current, rel_path)
    return resolved, current, enc, rel_path, f"Updated: {rel_path}\n{diff}", OperationType.UPDATE


def _compute_v4a_add(op: PatchOperation, resolved: Path) -> "_PendingWrite | str":
    """Compute content for a V4A ADD op. Returns pending tuple or error string."""
    if resolved.exists():
        return f"Cannot add {op.file_path} — file already exists (use Update File to modify)"
    content_lines = [
        line.content for hunk in op.hunks for line in hunk.lines if line.prefix == "+"
    ]
    return (
        resolved,
        "\n".join(content_lines),
        "utf-8",
        op.file_path,
        f"Created: {op.file_path}",
        OperationType.ADD,
    )


def _compute_v4a_delete(
    op: PatchOperation,
    resolved: Path,
    ctx: "RunContext[CoDeps]",
) -> "_PendingWrite | str":
    """Compute delete for a V4A DELETE op. Returns pending tuple or error string."""
    rel_path = op.file_path
    if not resolved.exists():
        return f"File not found for deletion: {rel_path}"
    if err := _check_patch_preconditions(resolved, rel_path, str(resolved), ctx):
        return err
    return resolved, None, "", rel_path, f"Deleted: {rel_path}", OperationType.DELETE


async def _write_v4a_pending(
    pending: "list[_PendingWrite]",
    ctx: "RunContext[CoDeps]",
) -> "tuple[str, list[str], list[str], list[str]] | str":
    """Write all computed V4A operations. Returns (display, modified, created, deleted) or error."""
    from co_cli.tools.resource_lock import ResourceBusyError

    _kind_to_list: dict[OperationType, list[str]] = {
        OperationType.UPDATE: [],
        OperationType.ADD: [],
        OperationType.DELETE: [],
    }
    display_parts: list[str] = []

    for resolved, content, enc, rel_path, display_chunk, kind in pending:
        try:
            async with ctx.deps.resource_locks.try_acquire(str(resolved)):
                if content is None:
                    resolved.unlink()
                else:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    resolved.write_text(content, encoding=enc)
                    ctx.deps.file_read_mtimes[str(resolved)] = _safe_mtime(resolved)
        except ResourceBusyError:
            return f"{rel_path} is being modified by another tool call — retry next turn"

        if content is not None:
            display_chunk = await _run_lint_if_python(resolved, display_chunk)
        _kind_to_list[kind].append(rel_path)
        display_parts.append(display_chunk)

    return (
        "\n\n".join(display_parts),
        _kind_to_list[OperationType.UPDATE],
        _kind_to_list[OperationType.ADD],
        _kind_to_list[OperationType.DELETE],
    )


async def _apply_v4a_patch(
    ops: list[PatchOperation],
    ctx: "RunContext[CoDeps]",
) -> "tuple[str, list[str], list[str], list[str]] | str":
    """Compute all V4A operations in memory, then write. Returns error string or result tuple."""
    workspace_root = ctx.deps.workspace_root
    pending: list[_PendingWrite] = []

    for op in ops:
        try:
            resolved = _enforce_workspace_boundary(Path(op.file_path), workspace_root)
        except ValueError as e:
            return str(e)

        if op.operation == OperationType.UPDATE:
            result = _compute_v4a_update(op, resolved, ctx)
        elif op.operation == OperationType.ADD:
            result = _compute_v4a_add(op, resolved)
        elif op.operation == OperationType.DELETE:
            result = _compute_v4a_delete(op, resolved, ctx)
        else:
            continue  # MOVE not supported; skip

        if isinstance(result, str):
            return result
        pending.append(result)

    return await _write_v4a_pending(pending, ctx)


async def _file_patch_replace(
    ctx: "RunContext[CoDeps]",
    path: str | None,
    old_string: str | None,
    new_string: str | None,
    replace_all: bool,
    show_diff: bool,
) -> ToolReturn:
    """Handle file_patch in replace mode (single-file targeted edit)."""
    if path is None:
        return tool_error("path is required in replace mode", ctx=ctx)
    if old_string is None:
        return tool_error("old_string is required in replace mode", ctx=ctx)
    if new_string is None:
        return tool_error("new_string is required in replace mode", ctx=ctx)

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


@agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, approval=True, retries=1)
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


@agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, approval=True, retries=1)
async def file_patch(
    ctx: RunContext[CoDeps],
    mode: str = "replace",
    path: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
    show_diff: bool = False,
    patch: str | None = None,
) -> ToolReturn:
    """Edit files by replacing a string with fuzzy matching, or apply a V4A multi-file patch.

    mode="replace" (default): targeted single-file edit.
        Requires file_read before patching. path, old_string, and new_string are required.
        old_string must be unique in the file; set replace_all=True to replace all occurrences.
        Tries four strategies: exact, line-trimmed, indent-stripped, escape-expanded.

    mode="patch": apply a V4A multi-file patch. patch argument is required.
        Supports Update File, Add File, and Delete File across multiple files.
        Update File and Delete File require file_read for each affected file first.

        V4A format:
            *** Begin Patch
            *** Update File: path/to/file.py
            @@ optional context hint @@
             context line
            -removed line
            +added line
            *** Add File: path/to/new.py
            +file content line
            *** Delete File: path/to/old.py
            *** End Patch

    When NOT to use: for creating new files or complete rewrites — use file_write instead.

    Args:
        mode: "replace" (default) for single-file edit, "patch" for V4A multi-file patch.
        path: File path relative to workspace root (required for replace mode).
        old_string: String to replace with fuzzy matching (required for replace mode).
        new_string: Replacement string applied verbatim (required for replace mode).
        replace_all: Replace all occurrences instead of requiring uniqueness (replace mode only).
        show_diff: Prepend a unified diff to output (replace mode only).
        patch: V4A format patch string (required for patch mode).
    """
    _VALID_MODES = {"replace", "patch"}
    if mode not in _VALID_MODES:
        return tool_error(f"Invalid mode {mode!r} — use 'replace' or 'patch'", ctx=ctx)

    if mode == "patch":
        if not patch:
            return tool_error("patch argument is required in patch mode", ctx=ctx)
        ops, parse_err = parse_v4a_patch(patch)
        if parse_err:
            return tool_error(f"Invalid patch: {parse_err}", ctx=ctx)
        result = await _apply_v4a_patch(ops, ctx)
        if isinstance(result, str):
            return tool_error(result, ctx=ctx)
        display, files_modified, files_created, files_deleted = result
        return tool_output(
            display,
            ctx=ctx,
            files_modified=files_modified,
            files_created=files_created,
            files_deleted=files_deleted,
        )

    return await _file_patch_replace(ctx, path, old_string, new_string, replace_all, show_diff)
