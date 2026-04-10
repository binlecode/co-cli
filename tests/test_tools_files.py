"""Functional tests for native file tools."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps
from co_cli.tools.files import (
    _enforce_workspace_boundary,
    _is_recursive_pattern,
    edit_file,
    find_in_files,
    list_directory,
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


# --- list_directory ---


@pytest.mark.asyncio
async def test_list_directory_basic(tmp_path):
    """Lists files and subdirectories in a workspace directory."""
    (tmp_path / "a.py").write_text("hello")
    (tmp_path / "b.txt").write_text("world")
    (tmp_path / "subdir").mkdir()

    result = await list_directory(_make_ctx(tmp_path), path=".")

    assert not result.metadata.get("error")
    assert result.metadata["count"] >= 3
    names = [e["name"] for e in result.metadata["entries"]]
    assert "a.py" in names
    assert "b.txt" in names
    assert "subdir" in names


@pytest.mark.asyncio
async def test_list_directory_pattern(tmp_path):
    """Pattern filter restricts results to matching files only."""
    (tmp_path / "main.py").write_text("")
    (tmp_path / "util.py").write_text("")
    (tmp_path / "readme.txt").write_text("")

    result = await list_directory(_make_ctx(tmp_path), path=".", pattern="*.py")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert "main.py" in names
    assert "util.py" in names
    assert "readme.txt" not in names


@pytest.mark.asyncio
async def test_list_directory_recursive_glob(tmp_path):
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

    result = await list_directory(_make_ctx(tmp_path), path=".", pattern="**/*.py")

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
async def test_list_directory_recursive_truncation(tmp_path):
    """Recursive glob truncates at max_entries and reports truncation."""
    for i in range(10):
        (tmp_path / f"file{i:02d}.py").write_text("")

    result = await list_directory(_make_ctx(tmp_path), path=".", pattern="**/*.py", max_entries=3)

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 3
    assert result.metadata["truncated"] is True
    assert "truncated" in result.return_value


@pytest.mark.asyncio
async def test_list_directory_broken_symlink(tmp_path):
    """Broken symlinks do not crash recursive glob — they appear in results."""
    (tmp_path / "real.txt").write_text("exists")
    (tmp_path / "broken_link").symlink_to(tmp_path / "nonexistent_target")

    result = await list_directory(_make_ctx(tmp_path), path=".", pattern="**/*")

    assert not result.metadata.get("error")
    names = [e["name"] for e in result.metadata["entries"]]
    assert any("broken_link" in n for n in names)
    assert any("real.txt" in n for n in names)


@pytest.mark.asyncio
async def test_list_directory_shallow_truncation(tmp_path):
    """Shallow listing truncates at max_entries and reports truncation."""
    for i in range(10):
        (tmp_path / f"file{i:02d}.txt").write_text("")

    result = await list_directory(_make_ctx(tmp_path), path=".", max_entries=3)

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
async def test_list_directory_not_found(tmp_path):
    """Returns error dict when path does not exist."""
    result = await list_directory(_make_ctx(tmp_path), path="nonexistent_dir")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_list_directory_not_a_dir(tmp_path):
    """Returns error dict when path points to a file, not a directory."""
    (tmp_path / "afile.txt").write_text("content")

    result = await list_directory(_make_ctx(tmp_path), path="afile.txt")

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


# --- find_in_files ---


@pytest.mark.asyncio
async def test_find_in_files(tmp_path):
    """Finds regex matches across files; only files containing pattern are returned."""
    (tmp_path / "alpha.txt").write_text("foo bar\nbaz\n")
    (tmp_path / "beta.txt").write_text("hello foo\n")
    (tmp_path / "gamma.txt").write_text("nothing here\n")

    result = await find_in_files(_make_ctx(tmp_path), pattern="foo")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 2
    files_matched = {m["file"] for m in result.metadata["matches"]}
    assert "alpha.txt" in files_matched
    assert "beta.txt" in files_matched
    assert "gamma.txt" not in files_matched


@pytest.mark.asyncio
async def test_find_in_files_invalid_regex(tmp_path):
    """Returns error dict for malformed regex patterns."""
    result = await find_in_files(_make_ctx(tmp_path), pattern="[unclosed")

    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_find_in_files_no_matches(tmp_path):
    """Returns zero count and empty matches list when nothing matches."""
    (tmp_path / "sample.txt").write_text("no match here\n")

    result = await find_in_files(_make_ctx(tmp_path), pattern="zzz_will_never_match")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 0
    assert result.metadata["matches"] == []


@pytest.mark.asyncio
async def test_find_in_files_recursive_subdirectory(tmp_path):
    """Recursive glob reaches files in deeply nested subdirectories."""
    deep = tmp_path / "src" / "pkg"
    deep.mkdir(parents=True)
    (deep / "deep.py").write_text("TARGET_MARKER = True\n")
    (tmp_path / "top.txt").write_text("no match here\n")

    result = await find_in_files(_make_ctx(tmp_path), pattern="TARGET_MARKER")

    assert not result.metadata.get("error")
    assert result.metadata["count"] == 1
    assert "src/pkg/deep.py" in result.metadata["matches"][0]["file"]


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


# --- edit_file ---


@pytest.mark.asyncio
async def test_edit_file_single(tmp_path):
    """Replaces a unique search string in a file."""
    (tmp_path / "config.txt").write_text("host=localhost\nport=8080\n")

    result = await edit_file(
        _make_ctx(tmp_path), path="config.txt", search="localhost", replacement="example.com"
    )

    assert not result.metadata.get("error")
    assert result.metadata["replacements"] == 1
    assert (tmp_path / "config.txt").read_text() == "host=example.com\nport=8080\n"


@pytest.mark.asyncio
async def test_edit_file_not_found(tmp_path):
    """Raises ValueError when search string is absent from the file."""
    (tmp_path / "notes.txt").write_text("some content here\n")

    with pytest.raises(ValueError, match="Search string not found"):
        await edit_file(
            _make_ctx(tmp_path), path="notes.txt", search="absent_string", replacement="x"
        )


@pytest.mark.asyncio
async def test_edit_file_multiple_no_replace_all(tmp_path):
    """Raises ValueError when search string appears multiple times without replace_all=True."""
    (tmp_path / "repeat.txt").write_text("foo\nfoo\nbar\n")

    with pytest.raises(ValueError, match="Found 2 occurrences"):
        await edit_file(_make_ctx(tmp_path), path="repeat.txt", search="foo", replacement="baz")


@pytest.mark.asyncio
async def test_edit_file_replace_all(tmp_path):
    """Replaces all occurrences when replace_all=True."""
    (tmp_path / "multi.txt").write_text("foo\nfoo\nbar\n")

    result = await edit_file(
        _make_ctx(tmp_path), path="multi.txt", search="foo", replacement="qux", replace_all=True
    )

    assert not result.metadata.get("error")
    assert result.metadata["replacements"] == 2
    assert (tmp_path / "multi.txt").read_text() == "qux\nqux\nbar\n"


@pytest.mark.asyncio
async def test_edit_file_not_found_path(tmp_path):
    """Returns error dict when the target file does not exist."""
    result = await edit_file(_make_ctx(tmp_path), path="ghost.txt", search="x", replacement="y")

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
