"""Functional tests for native file tools."""

import pytest
from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.deps import CoDeps, CoConfig
from co_cli.tools._shell_backend import ShellBackend
from co_cli.tools.files import (
    list_directory,
    read_file,
    find_in_files,
    write_file,
    edit_file,
)

# Cache agent at module level — build_agent() is expensive; model reference is stable.
_AGENT = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))


def _make_ctx(workspace: Path) -> RunContext:
    """Return a real RunContext scoped to a workspace directory."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(workspace_root=workspace),
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

    assert not result.get("error")
    assert result["count"] >= 3
    names = [e["name"] for e in result["entries"]]
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

    assert not result.get("error")
    names = [e["name"] for e in result["entries"]]
    assert "main.py" in names
    assert "util.py" in names
    assert "readme.txt" not in names


@pytest.mark.asyncio
async def test_list_directory_not_found(tmp_path):
    """Returns error dict when path does not exist."""
    result = await list_directory(_make_ctx(tmp_path), path="nonexistent_dir")

    assert result.get("error") is True


@pytest.mark.asyncio
async def test_list_directory_not_a_dir(tmp_path):
    """Returns error dict when path points to a file, not a directory."""
    (tmp_path / "afile.txt").write_text("content")

    result = await list_directory(_make_ctx(tmp_path), path="afile.txt")

    assert result.get("error") is True


# --- read_file ---


@pytest.mark.asyncio
async def test_read_file_full(tmp_path):
    """Reads complete file content and returns it in display."""
    (tmp_path / "hello.txt").write_text("hello\nworld\n")

    result = await read_file(_make_ctx(tmp_path), path="hello.txt")

    assert not result.get("error")
    assert "hello" in result["display"]
    assert "world" in result["display"]
    assert result["lines"] == 2


@pytest.mark.asyncio
async def test_read_file_line_range(tmp_path):
    """Reads only the requested line range (1-indexed, inclusive)."""
    lines = [f"line{i}\n" for i in range(1, 11)]
    (tmp_path / "numbered.txt").write_text("".join(lines))

    result = await read_file(_make_ctx(tmp_path), path="numbered.txt", start_line=3, end_line=7)

    assert not result.get("error")
    assert result["lines"] == 10
    display = result["display"]
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

    assert result.get("error") is True


@pytest.mark.asyncio
async def test_read_file_path_is_dir(tmp_path):
    """Returns error dict when path points to a directory."""
    (tmp_path / "adir").mkdir()

    result = await read_file(_make_ctx(tmp_path), path="adir")

    assert result.get("error") is True


# --- find_in_files ---


@pytest.mark.asyncio
async def test_find_in_files(tmp_path):
    """Finds regex matches across files; only files containing pattern are returned."""
    (tmp_path / "alpha.txt").write_text("foo bar\nbaz\n")
    (tmp_path / "beta.txt").write_text("hello foo\n")
    (tmp_path / "gamma.txt").write_text("nothing here\n")

    result = await find_in_files(_make_ctx(tmp_path), pattern="foo")

    assert not result.get("error")
    assert result["count"] == 2
    files_matched = {m["file"] for m in result["matches"]}
    assert "alpha.txt" in files_matched
    assert "beta.txt" in files_matched
    assert "gamma.txt" not in files_matched


@pytest.mark.asyncio
async def test_find_in_files_invalid_regex(tmp_path):
    """Returns error dict for malformed regex patterns."""
    result = await find_in_files(_make_ctx(tmp_path), pattern="[unclosed")

    assert result.get("error") is True


@pytest.mark.asyncio
async def test_find_in_files_no_matches(tmp_path):
    """Returns zero count and empty matches list when nothing matches."""
    (tmp_path / "sample.txt").write_text("no match here\n")

    result = await find_in_files(_make_ctx(tmp_path), pattern="zzz_will_never_match")

    assert not result.get("error")
    assert result["count"] == 0
    assert result["matches"] == []


# --- write_file ---


@pytest.mark.asyncio
async def test_write_file_creates_dirs(tmp_path):
    """Creates parent directories automatically when writing to nested path."""
    result = await write_file(_make_ctx(tmp_path), path="deep/nested/dir/file.txt", content="test content")

    assert not result.get("error")
    written = tmp_path / "deep" / "nested" / "dir" / "file.txt"
    assert written.exists()
    assert written.read_text() == "test content"


@pytest.mark.asyncio
async def test_write_file_overwrites_existing(tmp_path):
    """Overwrites file content when file already exists."""
    (tmp_path / "existing.txt").write_text("old content")

    result = await write_file(_make_ctx(tmp_path), path="existing.txt", content="new content")

    assert not result.get("error")
    assert (tmp_path / "existing.txt").read_text() == "new content"


@pytest.mark.asyncio
async def test_write_file_returns_byte_count(tmp_path):
    """Returned byte count matches actual written bytes."""
    content = "hello"
    result = await write_file(_make_ctx(tmp_path), path="bytes.txt", content=content)

    assert not result.get("error")
    assert result["bytes"] == len(content.encode("utf-8"))


@pytest.mark.asyncio
async def test_write_file_path_escape(tmp_path):
    """Returns error dict when path escapes the workspace root."""
    result = await write_file(_make_ctx(tmp_path), path="../../etc/passwd", content="evil")

    assert result.get("error") is True


# --- edit_file ---


@pytest.mark.asyncio
async def test_edit_file_single(tmp_path):
    """Replaces a unique search string in a file."""
    (tmp_path / "config.txt").write_text("host=localhost\nport=8080\n")

    result = await edit_file(_make_ctx(tmp_path), path="config.txt", search="localhost", replacement="example.com")

    assert not result.get("error")
    assert result["replacements"] == 1
    assert (tmp_path / "config.txt").read_text() == "host=example.com\nport=8080\n"


@pytest.mark.asyncio
async def test_edit_file_not_found(tmp_path):
    """Raises ValueError when search string is absent from the file."""
    (tmp_path / "notes.txt").write_text("some content here\n")

    with pytest.raises(ValueError, match="Search string not found"):
        await edit_file(_make_ctx(tmp_path), path="notes.txt", search="absent_string", replacement="x")


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

    assert not result.get("error")
    assert result["replacements"] == 2
    assert (tmp_path / "multi.txt").read_text() == "qux\nqux\nbar\n"


@pytest.mark.asyncio
async def test_edit_file_not_found_path(tmp_path):
    """Returns error dict when the target file does not exist."""
    result = await edit_file(_make_ctx(tmp_path), path="ghost.txt", search="x", replacement="y")

    assert result.get("error") is True
