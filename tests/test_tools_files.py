"""Functional tests for native file tools."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.tools.files import (
    _enforce_workspace_boundary,
    _is_recursive_pattern,
    glob,
    grep,
    patch,
    read_file,
    write_file,
)
from co_cli.tools.shell_backend import ShellBackend

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=settings)


def _make_ctx(workspace: Path) -> RunContext:
    """Return a real RunContext scoped to a workspace directory."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=make_settings(),
        workspace_root=workspace,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


# --- glob ---


@pytest.mark.asyncio
async def test_glob_basic(tmp_path):
    """Lists files and subdirectories in a workspace directory."""
    (tmp_path / "a.py").write_text("hello")
    (tmp_path / "b.txt").write_text("world")
    (tmp_path / "subdir").mkdir()

    result = await glob(_make_ctx(tmp_path), path=".")

    assert not result.metadata.get("error")
    assert result.metadata["count"] >= 3
    names = [e["name"] for e in result.metadata["entries"]]
    assert "a.py" in names
    assert "b.txt" in names
    assert "subdir" in names


@pytest.mark.asyncio
async def test_glob_pattern(tmp_path):
    """Pattern filter restricts results to matching files only."""
    (tmp_path / "main.py").write_text("")
    (tmp_path / "util.py").write_text("")
    (tmp_path / "readme.txt").write_text("")

    result = await glob(_make_ctx(tmp_path), path=".", pattern="*.py")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert "main.py" in names
    assert "util.py" in names
    assert "readme.txt" not in names


@pytest.mark.asyncio
async def test_glob_recursive_glob(tmp_path):
    """Recursive glob pattern finds files across nested directories, sorted by mtime."""
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    # Create files with controlled mtime ordering
    import time

    (tmp_path / "root.py").write_text("r")
    time.sleep(0.05)
    (sub / "deep.py").write_text("d")
    time.sleep(0.05)
    (tmp_path / "src" / "mid.py").write_text("m")
    (tmp_path / "readme.txt").write_text("ignore me")

    result = await glob(_make_ctx(tmp_path), path=".", pattern="**/*.py")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert len(names) == 3
    # All three .py files found, .txt excluded
    assert any("root.py" in n for n in names)
    assert any("deep.py" in n for n in names)
    assert any("mid.py" in n for n in names)
    assert not any(".txt" in n for n in names)
    # Newest first (mid.py was written last)
    assert "mid.py" in names[0]


@pytest.mark.asyncio
async def test_glob_recursive_truncation(tmp_path):
    """Recursive glob truncates at max_entries and reports truncation."""
    for i in range(10):
        (tmp_path / f"file{i:02d}.py").write_text("")

    result = await glob(_make_ctx(tmp_path), path=".", pattern="**/*.py", max_entries=3)

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 3
    assert result.metadata["truncated"] is True
    assert "truncated" in result.return_value


@pytest.mark.asyncio
async def test_glob_broken_symlink(tmp_path):
    """Broken symlinks do not crash recursive glob — they appear in results."""
    (tmp_path / "real.txt").write_text("exists")
    (tmp_path / "broken_link").symlink_to(tmp_path / "nonexistent_target")

    result = await glob(_make_ctx(tmp_path), path=".", pattern="**/*")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert any("broken_link" in n for n in names)
    assert any("real.txt" in n for n in names)


@pytest.mark.asyncio
async def test_glob_shallow_truncation(tmp_path):
    """Shallow listing truncates at max_entries and reports truncation."""
    for i in range(10):
        (tmp_path / f"file{i:02d}.txt").write_text("")

    result = await glob(_make_ctx(tmp_path), path=".", max_entries=3)

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 3
    assert result.metadata["truncated"] is True


def test_is_recursive_pattern():
    """Recursive pattern detection for ** and path separators."""
    assert _is_recursive_pattern("**/*.py") is True
    assert _is_recursive_pattern("src/**/*.ts") is True
    assert _is_recursive_pattern("src/pkg/*.py") is True
    assert _is_recursive_pattern("*.py") is False
    assert _is_recursive_pattern("*") is False


@pytest.mark.asyncio
async def test_glob_not_found(tmp_path):
    """Returns error dict when path does not exist."""
    result = await glob(_make_ctx(tmp_path), path="nonexistent_dir")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_glob_not_a_dir(tmp_path):
    """Returns error dict when path points to a file, not a directory."""
    (tmp_path / "afile.txt").write_text("content")

    result = await glob(_make_ctx(tmp_path), path="afile.txt")

    assert result.metadata.get("error") is True


# --- read_file ---


@pytest.mark.asyncio
async def test_read_file_full(tmp_path):
    """Reads complete file content and returns it in display."""
    (tmp_path / "hello.txt").write_text("hello\nworld\n")

    result = await read_file(_make_ctx(tmp_path), path="hello.txt")

    assert not result.metadata.get("error")
    assert "hello" in result.return_value
    assert "world" in result.return_value
    assert result.metadata["lines"] == 2


@pytest.mark.asyncio
async def test_read_file_line_range(tmp_path):
    """Reads only the requested line range (1-indexed, inclusive)."""
    lines = [f"line{i}\n" for i in range(1, 11)]
    (tmp_path / "numbered.txt").write_text("".join(lines))

    result = await read_file(_make_ctx(tmp_path), path="numbered.txt", start_line=3, end_line=7)

    assert not result.metadata.get("error")
    assert result.metadata["lines"] == 10
    display = result.return_value
    assert "line3" in display
    assert "line7" in display
    # Lines outside the range must not appear
    assert "line1" not in display
    assert "line2" not in display
    assert "line8" not in display


@pytest.mark.asyncio
async def test_read_file_not_found(tmp_path):
    """Returns error dict when file does not exist."""
    result = await read_file(_make_ctx(tmp_path), path="missing.txt")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_read_file_path_is_dir(tmp_path):
    """Returns error dict when path points to a directory."""
    (tmp_path / "adir").mkdir()

    result = await read_file(_make_ctx(tmp_path), path="adir")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_read_file_binary(tmp_path):
    """Returns error for binary files instead of crashing with UnicodeDecodeError."""
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")

    result = await read_file(_make_ctx(tmp_path), path="image.png")

    assert result.metadata.get("error") is True
    assert "Binary file" in result.return_value


@pytest.mark.asyncio
async def test_read_file_path_escape(tmp_path):
    """Returns error when path escapes workspace — tool-level wiring test.

    Complements test_enforce_workspace_boundary_escape_blocked which tests the
    boundary function directly. This test verifies read_file catches the ValueError
    and converts it to tool_error.
    """
    result = await read_file(_make_ctx(tmp_path), path="../../etc/passwd")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_read_file_line_numbers(tmp_path):
    """Full-file read returns cat -n style numbered output."""
    (tmp_path / "five.txt").write_text("aaa\nbbb\nccc\nddd\neee\n")

    result = await read_file(_make_ctx(tmp_path), path="five.txt")

    assert not result.metadata.get("error")
    assert "     1\t" in result.return_value
    assert "     5\t" in result.return_value


@pytest.mark.asyncio
async def test_read_file_line_numbers_ranged(tmp_path):
    """Ranged read line numbers reflect actual file positions."""
    lines = [f"line{i}\n" for i in range(1, 11)]
    (tmp_path / "ten.txt").write_text("".join(lines))

    result = await read_file(_make_ctx(tmp_path), path="ten.txt", start_line=3, end_line=5)

    assert not result.metadata.get("error")
    # First line in output should be numbered 3
    first_line = result.return_value.split("\n")[0]
    assert first_line.startswith("     3\t")
    assert "     5\t" in result.return_value


# --- grep ---


@pytest.mark.asyncio
async def test_grep(tmp_path):
    """Finds regex matches across files; only files containing pattern are returned."""
    (tmp_path / "alpha.txt").write_text("foo bar\nbaz\n")
    (tmp_path / "beta.txt").write_text("hello foo\n")
    (tmp_path / "gamma.txt").write_text("nothing here\n")

    result = await grep(_make_ctx(tmp_path), pattern="foo")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 2
    # Content mode output: "file:line: text" — verify which files appear
    assert "alpha.txt" in result.return_value
    assert "beta.txt" in result.return_value
    assert "gamma.txt" not in result.return_value


@pytest.mark.asyncio
async def test_grep_invalid_regex(tmp_path):
    """Returns error dict for malformed regex patterns."""
    result = await grep(_make_ctx(tmp_path), pattern="[unclosed")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_grep_no_matches(tmp_path):
    """Returns zero count and empty matches list when nothing matches."""
    (tmp_path / "sample.txt").write_text("no match here\n")

    result = await grep(_make_ctx(tmp_path), pattern="zzz_will_never_match")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 0
    assert "(no matches)" in result.return_value


@pytest.mark.asyncio
async def test_grep_recursive_subdirectory(tmp_path):
    """Recursive glob reaches files in deeply nested subdirectories."""
    deep = tmp_path / "src" / "pkg"
    deep.mkdir(parents=True)
    (deep / "deep.py").write_text("TARGET_MARKER = True\n")
    (tmp_path / "top.txt").write_text("no match here\n")

    result = await grep(_make_ctx(tmp_path), pattern="TARGET_MARKER")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 1
    assert "src/pkg/deep.py" in result.return_value


# --- write_file ---


@pytest.mark.asyncio
async def test_write_file_creates_dirs(tmp_path):
    """Creates parent directories automatically when writing to nested path."""
    result = await write_file(
        _make_ctx(tmp_path), path="deep/nested/dir/file.txt", content="test content"
    )

    assert not result.metadata.get("error")
    written = tmp_path / "deep" / "nested" / "dir" / "file.txt"
    assert written.exists()
    assert written.read_text() == "test content"


@pytest.mark.asyncio
async def test_write_file_overwrites_existing(tmp_path):
    """Overwrites file content when file already exists."""
    (tmp_path / "existing.txt").write_text("old content")

    result = await write_file(_make_ctx(tmp_path), path="existing.txt", content="new content")

    assert not result.metadata.get("error")
    assert (tmp_path / "existing.txt").read_text() == "new content"


@pytest.mark.asyncio
async def test_write_file_returns_byte_count(tmp_path):
    """Returned byte count matches actual written bytes."""
    content = "hello"
    result = await write_file(_make_ctx(tmp_path), path="bytes.txt", content=content)

    assert not result.metadata.get("error")
    assert result.metadata["bytes"] == len(content.encode("utf-8"))


@pytest.mark.asyncio
async def test_write_file_path_escape(tmp_path):
    """Returns error dict when path escapes the workspace root."""
    result = await write_file(_make_ctx(tmp_path), path="../../etc/passwd", content="evil")

    assert result.metadata.get("error") is True


# --- patch ---


@pytest.mark.asyncio
async def test_patch_exact_match(tmp_path):
    """Exact strategy replaces a unique string in a file."""
    (tmp_path / "config.txt").write_text("host=localhost\nport=8080\n")
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="config.txt")

    result = await patch(ctx, path="config.txt", old_string="localhost", new_string="example.com")

    assert not result.metadata.get("error")
    assert result.metadata["replacements"] == 1
    assert result.metadata["strategy"] == "exact"
    assert (tmp_path / "config.txt").read_text() == "host=example.com\nport=8080\n"


@pytest.mark.asyncio
async def test_patch_line_trimmed(tmp_path):
    """line-trimmed strategy matches when old_string has trailing whitespace per line."""
    (tmp_path / "code.py").write_text("def foo():\n    return 1\n")
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="code.py")

    # old_string has trailing spaces on each line — not present in file
    result = await patch(
        ctx,
        path="code.py",
        old_string="def foo():  \n    return 1  \n",
        new_string="def bar():\n    return 2\n",
    )

    assert not result.metadata.get("error")
    assert result.metadata["strategy"] == "line-trimmed"
    assert (tmp_path / "code.py").read_text() == "def bar():\n    return 2\n"


@pytest.mark.asyncio
async def test_patch_indent_stripped(tmp_path):
    """Fuzzy matching succeeds when old_string uses wrong indentation (tabs vs spaces)."""
    (tmp_path / "src.py").write_text("    x = 1\n    y = 2\n")
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="src.py")

    # old_string uses tabs instead of spaces — a fuzzy strategy normalises the indentation
    result = await patch(
        ctx,
        path="src.py",
        old_string="\tx = 1\n\ty = 2\n",
        new_string="    x = 10\n    y = 20\n",
    )

    assert not result.metadata.get("error")
    assert result.metadata["replacements"] == 1
    assert (tmp_path / "src.py").read_text() == "    x = 10\n    y = 20\n"


@pytest.mark.asyncio
async def test_patch_escape_expanded(tmp_path):
    """escape-expanded strategy matches when old_string uses literal \\n instead of actual newline."""
    (tmp_path / "data.txt").write_text("line one\nline two\n")
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="data.txt")

    # old_string has literal backslash-n — should match actual newline in file
    result = await patch(
        ctx,
        path="data.txt",
        old_string="line one\\nline two\\n",
        new_string="replaced\n",
    )

    assert not result.metadata.get("error")
    assert result.metadata["strategy"] == "escape-expanded"
    assert (tmp_path / "data.txt").read_text() == "replaced\n"


@pytest.mark.asyncio
async def test_patch_ambiguous_returns_error(tmp_path):
    """Returns error when old_string matches multiple times without replace_all=True."""
    (tmp_path / "repeat.txt").write_text("foo\nfoo\nbar\n")
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="repeat.txt")

    result = await patch(ctx, path="repeat.txt", old_string="foo", new_string="baz")

    assert result.metadata.get("error") is True
    assert "2" in result.return_value


@pytest.mark.asyncio
async def test_patch_no_match_returns_error(tmp_path):
    """Returns error when no strategy matches — error mentions the file."""
    (tmp_path / "notes.txt").write_text("some content here\n")
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="notes.txt")

    result = await patch(ctx, path="notes.txt", old_string="absent_string", new_string="x")

    assert result.metadata.get("error") is True
    assert "notes.txt" in result.return_value


@pytest.mark.asyncio
async def test_patch_not_found_path(tmp_path):
    """Returns error dict when the target file does not exist."""
    result = await patch(_make_ctx(tmp_path), path="ghost.txt", old_string="x", new_string="y")

    assert result.metadata.get("error") is True


# --- _enforce_workspace_boundary ---


def test_enforce_workspace_boundary_relative_path(tmp_path):
    """Relative path is resolved to absolute path within workspace."""
    (tmp_path / "sub").mkdir()
    resolved = _enforce_workspace_boundary(Path("sub/file.txt"), tmp_path)
    assert resolved == (tmp_path / "sub" / "file.txt").resolve()
    assert resolved.is_absolute()


def test_enforce_workspace_boundary_absolute_within(tmp_path):
    """Absolute path within workspace passes through unchanged."""
    target = tmp_path / "inner" / "doc.txt"
    resolved = _enforce_workspace_boundary(target, tmp_path)
    assert resolved == target.resolve()


def test_enforce_workspace_boundary_escape_blocked(tmp_path):
    """Path that escapes workspace root raises ValueError."""
    with pytest.raises(ValueError, match="Path escapes workspace"):
        _enforce_workspace_boundary(Path("../../etc/passwd"), tmp_path)


def test_enforce_workspace_boundary_symlink_escape_blocked(tmp_path):
    """Symlink that resolves outside workspace is blocked."""
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "sneaky_link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="Path escapes workspace"):
        _enforce_workspace_boundary(Path("sneaky_link"), tmp_path)


# --- CoToolLifecycle path normalization ---


@pytest.mark.asyncio
async def test_lifecycle_normalizes_relative_path(tmp_path):
    """CoToolLifecycle.before_tool_execute resolves relative paths to absolute for file tools."""
    from pydantic_ai.capabilities import ValidatedToolArgs
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from co_cli.context._tool_lifecycle import CoToolLifecycle

    lifecycle = CoToolLifecycle()
    ctx = _make_ctx(tmp_path)
    call = ToolCallPart(tool_name="read_file", args={"path": "subdir/file.txt"})
    tool_def = ToolDefinition(name="read_file", description="read", parameters_json_schema={})
    args: ValidatedToolArgs = {"path": "subdir/file.txt"}

    result_args = await lifecycle.before_tool_execute(
        ctx,
        call=call,
        tool_def=tool_def,
        args=args,
    )

    expected = str((tmp_path / "subdir" / "file.txt").resolve())
    assert result_args["path"] == expected


@pytest.mark.asyncio
async def test_lifecycle_skips_non_file_tools(tmp_path):
    """CoToolLifecycle.before_tool_execute leaves non-file-tool args unchanged."""
    from pydantic_ai.capabilities import ValidatedToolArgs
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from co_cli.context._tool_lifecycle import CoToolLifecycle

    lifecycle = CoToolLifecycle()
    ctx = _make_ctx(tmp_path)
    call = ToolCallPart(tool_name="web_search", args={"query": "hello"})
    tool_def = ToolDefinition(name="web_search", description="search", parameters_json_schema={})
    args: ValidatedToolArgs = {"query": "hello"}

    result_args = await lifecycle.before_tool_execute(
        ctx,
        call=call,
        tool_def=tool_def,
        args=args,
    )

    assert result_args == {"query": "hello"}


# --- staleness guard ---


@pytest.mark.asyncio
async def test_read_file_records_mtime(tmp_path):
    """read_file records the file's mtime in file_read_mtimes on success."""
    target = tmp_path / "tracked.txt"
    target.write_text("content")
    ctx = _make_ctx(tmp_path)

    await read_file(ctx, path="tracked.txt")

    assert str(target) in ctx.deps.file_read_mtimes
    assert ctx.deps.file_read_mtimes[str(target)] == target.stat().st_mtime


@pytest.mark.asyncio
async def test_write_file_staleness_blocked(tmp_path):
    """write_file returns error when file was modified since last read."""
    target = tmp_path / "stale.txt"
    target.write_text("original")
    ctx = _make_ctx(tmp_path)
    # Simulate: agent recorded an old mtime of 0.0 — file on disk has a newer mtime
    ctx.deps.file_read_mtimes[str(target)] = 0.0

    result = await write_file(ctx, path="stale.txt", content="overwrite")

    assert result.metadata.get("error") is True
    assert "changed since last read" in result.return_value


@pytest.mark.asyncio
async def test_patch_staleness_blocked(tmp_path):
    """patch returns error when file was modified since last read."""
    target = tmp_path / "stale.txt"
    target.write_text("original content")
    ctx = _make_ctx(tmp_path)
    ctx.deps.file_read_mtimes[str(target)] = 0.0

    result = await patch(ctx, path="stale.txt", old_string="original", new_string="new")

    assert result.metadata.get("error") is True
    assert "changed since last read" in result.return_value


@pytest.mark.asyncio
async def test_patch_requires_read_first(tmp_path):
    """patch returns error when file has never been read (not in file_read_mtimes)."""
    target = tmp_path / "unread.txt"
    target.write_text("original content")
    ctx = _make_ctx(tmp_path)
    # Do NOT call read_file — file_read_mtimes is empty

    result = await patch(ctx, path="unread.txt", old_string="original", new_string="replaced")

    assert result.metadata.get("error") is True
    assert "read_file" in result.return_value


@pytest.mark.asyncio
async def test_patch_return_preview(tmp_path):
    """patch return value includes old/new text preview after successful edit."""
    target = tmp_path / "code.py"
    target.write_text("x = 1\ny = 2\n")
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="code.py")

    result = await patch(ctx, path="code.py", old_string="x = 1", new_string="x = 99")

    assert not result.metadata.get("error")
    assert "x = 1" in result.return_value
    assert "x = 99" in result.return_value


@pytest.mark.asyncio
async def test_grep_scoped_to_path(tmp_path):
    """grep with path scopes search to the given subdirectory."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inside.py").write_text("TARGET_TOKEN = 1")
    (tmp_path / "outside.py").write_text("TARGET_TOKEN = 2")
    ctx = _make_ctx(tmp_path)

    result = await grep(ctx, pattern="TARGET_TOKEN", path="sub")

    assert not result.metadata.get("error")
    assert "inside.py" in result.return_value
    assert "outside.py" not in result.return_value


@pytest.mark.asyncio
async def test_write_file_new_file_skips_staleness(tmp_path):
    """write_file skips staleness check for paths that were never read."""
    ctx = _make_ctx(tmp_path)

    result = await write_file(ctx, path="brand_new.txt", content="hello")

    assert not result.metadata.get("error")


@pytest.mark.asyncio
async def test_write_file_staleness_mtime_updated_after_write(tmp_path):
    """write_file updates file_read_mtimes after a successful write so the second write does not false-positive."""
    target = tmp_path / "data.txt"
    target.write_text("initial")
    ctx = _make_ctx(tmp_path)

    # Read the file so mtime is registered
    await read_file(ctx, path="data.txt")

    result1 = await write_file(ctx, path="data.txt", content="first write")
    assert not result1.metadata.get("error")

    # Second write must also succeed — mtime was updated after the first write
    result2 = await write_file(ctx, path="data.txt", content="second write")
    assert not result2.metadata.get("error")


# --- file size guard + encoding detection ---


@pytest.mark.asyncio
async def test_patch_size_guard(tmp_path):
    """patch returns error when file exceeds _MAX_EDIT_BYTES."""
    from co_cli.tools.files import _MAX_EDIT_BYTES

    target = tmp_path / "huge.txt"
    target.write_bytes(b"x" * (_MAX_EDIT_BYTES + 1))
    ctx = _make_ctx(tmp_path)
    # Register the mtime directly — avoids reading 10 MB of data in this test
    ctx.deps.file_read_mtimes[str(target)] = target.stat().st_mtime

    result = await patch(ctx, path="huge.txt", old_string="x", new_string="y")

    assert result.metadata.get("error") is True
    assert "too large" in result.return_value


@pytest.mark.asyncio
async def test_read_file_utf16le(tmp_path):
    """read_file succeeds on UTF-16LE files with BOM."""
    target = tmp_path / "utf16.txt"
    # BOM (FF FE) + UTF-16LE payload
    target.write_bytes(b"\xff\xfe" + "hello utf16".encode("utf-16-le"))

    result = await read_file(_make_ctx(tmp_path), path="utf16.txt")

    assert not result.metadata.get("error")
    assert "hello utf16" in result.return_value


@pytest.mark.asyncio
async def test_patch_utf16(tmp_path):
    """patch succeeds on UTF-16 files with BOM — edit applies and file remains valid UTF-16."""
    target = tmp_path / "utf16edit.txt"
    target.write_bytes(b"\xff\xfe" + "hello world".encode("utf-16-le"))
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="utf16edit.txt")

    result = await patch(ctx, path="utf16edit.txt", old_string="hello", new_string="goodbye")

    assert not result.metadata.get("error")
    assert result.metadata["replacements"] == 1
    # Encoding must be preserved — file is still readable as UTF-16 with the replacement applied
    updated_content = target.read_text(encoding="utf-16")
    assert "goodbye world" in updated_content


# --- partial-read guard ---


@pytest.mark.asyncio
async def test_patch_partial_read_blocked(tmp_path):
    """patch returns error when only a line range of the file was read."""
    target = tmp_path / "large.py"
    target.write_text("line one\nline two\nline three\n")
    ctx = _make_ctx(tmp_path)
    # Only read lines 1-2 — file is in file_read_mtimes but also in file_partial_reads
    await read_file(ctx, path="large.py", start_line=1, end_line=2)

    result = await patch(ctx, path="large.py", old_string="line one", new_string="replaced")

    assert result.metadata.get("error") is True
    assert "start_line" in result.return_value or "end_line" in result.return_value


@pytest.mark.asyncio
async def test_patch_partial_clears_on_full_read(tmp_path):
    """A full read after a partial read clears the partial flag and allows patch."""
    target = tmp_path / "data.py"
    target.write_text("alpha\nbeta\ngamma\n")
    ctx = _make_ctx(tmp_path)
    await read_file(ctx, path="data.py", start_line=1, end_line=1)
    # Confirm patch is blocked after partial read
    blocked = await patch(ctx, path="data.py", old_string="alpha", new_string="x")
    assert blocked.metadata.get("error") is True

    # Full read clears the partial flag
    await read_file(ctx, path="data.py")
    result = await patch(ctx, path="data.py", old_string="alpha", new_string="x")
    assert not result.metadata.get("error")


# --- grep extended capabilities ---


@pytest.mark.asyncio
async def test_grep_case_insensitive(tmp_path):
    """grep with case_insensitive=True matches regardless of case."""
    (tmp_path / "a.txt").write_text("Hello World\n")
    ctx = _make_ctx(tmp_path)

    result = await grep(ctx, pattern="hello world", case_insensitive=True)

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 1
    assert "a.txt" in result.return_value


@pytest.mark.asyncio
async def test_grep_files_with_matches_mode(tmp_path):
    """output_mode='files_with_matches' returns only file paths, not line content."""
    (tmp_path / "hit.txt").write_text("TARGET here\n")
    (tmp_path / "miss.txt").write_text("nothing relevant\n")
    ctx = _make_ctx(tmp_path)

    result = await grep(ctx, pattern="TARGET", output_mode="files_with_matches")

    assert not result.metadata.get("error")
    assert "hit.txt" in result.return_value
    assert "miss.txt" not in result.return_value
    # Output is just the file path, no line numbers
    assert ":" not in result.return_value


@pytest.mark.asyncio
async def test_grep_count_mode(tmp_path):
    """output_mode='count' returns file: N for each matched file."""
    (tmp_path / "many.txt").write_text("MARK\nMARK\nno\n")
    (tmp_path / "one.txt").write_text("MARK\n")
    ctx = _make_ctx(tmp_path)

    result = await grep(ctx, pattern="MARK", output_mode="count")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 3  # 2 + 1 total matches
    assert "many.txt: 2" in result.return_value
    assert "one.txt: 1" in result.return_value


@pytest.mark.asyncio
async def test_grep_context_lines(tmp_path):
    """context_lines includes surrounding lines around each match."""
    (tmp_path / "src.py").write_text("before\nTARGET\nafter\n")
    ctx = _make_ctx(tmp_path)

    result = await grep(ctx, pattern="TARGET", context_lines=1)

    assert not result.metadata.get("error")
    assert "before" in result.return_value
    assert "TARGET" in result.return_value
    assert "after" in result.return_value


@pytest.mark.asyncio
async def test_grep_head_limit_and_offset(tmp_path):
    """head_limit caps output; offset skips the first N entries."""
    content = "\n".join(f"MATCH line {i}" for i in range(10))
    (tmp_path / "big.txt").write_text(content + "\n")
    ctx = _make_ctx(tmp_path)

    result_limited = await grep(ctx, pattern="MATCH", head_limit=3)
    assert not result_limited.metadata.get("error")
    assert result_limited.metadata["truncated"] is True
    assert result_limited.return_value.count("MATCH") == 3

    result_offset = await grep(ctx, pattern="MATCH", head_limit=3, offset=3)
    assert not result_offset.metadata.get("error")
    # First result_limited entry must not appear in result_offset
    first_line = result_limited.return_value.splitlines()[0]
    assert first_line not in result_offset.return_value
