"""Functional tests for native file tools."""

import pytest
from dataclasses import dataclass
from pathlib import Path

from co_cli.tools.files import (
    list_directory,
    read_file,
    find_in_files,
    write_file,
    edit_file,
)


@dataclass
class FakeDeps:
    pass


class FakeCtx:
    deps = FakeDeps()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Change cwd to a fresh tmp_path so all path resolution is scoped there."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- list_directory ---


@pytest.mark.asyncio
async def test_list_directory_basic(workspace):
    """Lists files and subdirectories in a workspace directory."""
    (workspace / "a.py").write_text("hello")
    (workspace / "b.txt").write_text("world")
    (workspace / "subdir").mkdir()

    result = await list_directory(FakeCtx(), path=".")

    assert not result.get("error")
    assert result["count"] >= 3
    names = [e["name"] for e in result["entries"]]
    assert "a.py" in names
    assert "b.txt" in names
    assert "subdir" in names


@pytest.mark.asyncio
async def test_list_directory_pattern(workspace):
    """Pattern filter restricts results to matching files only."""
    (workspace / "main.py").write_text("")
    (workspace / "util.py").write_text("")
    (workspace / "readme.txt").write_text("")

    result = await list_directory(FakeCtx(), path=".", pattern="*.py")

    assert not result.get("error")
    names = [e["name"] for e in result["entries"]]
    assert "main.py" in names
    assert "util.py" in names
    assert "readme.txt" not in names


@pytest.mark.asyncio
async def test_list_directory_not_found(workspace):
    """Returns error dict when path does not exist."""
    result = await list_directory(FakeCtx(), path="nonexistent_dir")

    assert result.get("error") is True


@pytest.mark.asyncio
async def test_list_directory_not_a_dir(workspace):
    """Returns error dict when path points to a file, not a directory."""
    (workspace / "afile.txt").write_text("content")

    result = await list_directory(FakeCtx(), path="afile.txt")

    assert result.get("error") is True


# --- read_file ---


@pytest.mark.asyncio
async def test_read_file_full(workspace):
    """Reads complete file content and returns it in display."""
    (workspace / "hello.txt").write_text("hello\nworld\n")

    result = await read_file(FakeCtx(), path="hello.txt")

    assert not result.get("error")
    assert "hello" in result["display"]
    assert "world" in result["display"]
    assert result["lines"] == 2


@pytest.mark.asyncio
async def test_read_file_line_range(workspace):
    """Reads only the requested line range (1-indexed, inclusive)."""
    lines = [f"line{i}\n" for i in range(1, 11)]
    (workspace / "numbered.txt").write_text("".join(lines))

    result = await read_file(FakeCtx(), path="numbered.txt", start_line=3, end_line=7)

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
async def test_read_file_not_found(workspace):
    """Returns error dict when file does not exist."""
    result = await read_file(FakeCtx(), path="missing.txt")

    assert result.get("error") is True


@pytest.mark.asyncio
async def test_read_file_path_is_dir(workspace):
    """Returns error dict when path points to a directory."""
    (workspace / "adir").mkdir()

    result = await read_file(FakeCtx(), path="adir")

    assert result.get("error") is True


# --- find_in_files ---


@pytest.mark.asyncio
async def test_find_in_files(workspace):
    """Finds regex matches across files; only files containing pattern are returned."""
    (workspace / "alpha.txt").write_text("foo bar\nbaz\n")
    (workspace / "beta.txt").write_text("hello foo\n")
    (workspace / "gamma.txt").write_text("nothing here\n")

    result = await find_in_files(FakeCtx(), pattern="foo")

    assert not result.get("error")
    assert result["count"] == 2
    files_matched = {m["file"] for m in result["matches"]}
    assert "alpha.txt" in files_matched
    assert "beta.txt" in files_matched
    assert "gamma.txt" not in files_matched


@pytest.mark.asyncio
async def test_find_in_files_invalid_regex(workspace):
    """Returns error dict for malformed regex patterns."""
    result = await find_in_files(FakeCtx(), pattern="[unclosed")

    assert result.get("error") is True


@pytest.mark.asyncio
async def test_find_in_files_no_matches(workspace):
    """Returns zero count and empty matches list when nothing matches."""
    (workspace / "sample.txt").write_text("no match here\n")

    result = await find_in_files(FakeCtx(), pattern="zzz_will_never_match")

    assert not result.get("error")
    assert result["count"] == 0
    assert result["matches"] == []


# --- write_file ---


@pytest.mark.asyncio
async def test_write_file_creates_dirs(workspace):
    """Creates parent directories automatically when writing to nested path."""
    result = await write_file(FakeCtx(), path="deep/nested/dir/file.txt", content="test content")

    assert not result.get("error")
    written = workspace / "deep" / "nested" / "dir" / "file.txt"
    assert written.exists()
    assert written.read_text() == "test content"


@pytest.mark.asyncio
async def test_write_file_overwrites_existing(workspace):
    """Overwrites file content when file already exists."""
    (workspace / "existing.txt").write_text("old content")

    result = await write_file(FakeCtx(), path="existing.txt", content="new content")

    assert not result.get("error")
    assert (workspace / "existing.txt").read_text() == "new content"


@pytest.mark.asyncio
async def test_write_file_returns_byte_count(workspace):
    """Returned byte count matches actual written bytes."""
    content = "hello"
    result = await write_file(FakeCtx(), path="bytes.txt", content=content)

    assert not result.get("error")
    assert result["bytes"] == len(content.encode("utf-8"))


@pytest.mark.asyncio
async def test_write_file_path_escape(workspace):
    """Returns error dict when path escapes the workspace root."""
    result = await write_file(FakeCtx(), path="../../etc/passwd", content="evil")

    assert result.get("error") is True


# --- edit_file ---


@pytest.mark.asyncio
async def test_edit_file_single(workspace):
    """Replaces a unique search string in a file."""
    (workspace / "config.txt").write_text("host=localhost\nport=8080\n")

    result = await edit_file(FakeCtx(), path="config.txt", search="localhost", replacement="example.com")

    assert not result.get("error")
    assert result["replacements"] == 1
    assert (workspace / "config.txt").read_text() == "host=example.com\nport=8080\n"


@pytest.mark.asyncio
async def test_edit_file_not_found(workspace):
    """Raises ValueError when search string is absent from the file."""
    (workspace / "notes.txt").write_text("some content here\n")

    with pytest.raises(ValueError, match="Search string not found"):
        await edit_file(FakeCtx(), path="notes.txt", search="absent_string", replacement="x")


@pytest.mark.asyncio
async def test_edit_file_multiple_no_replace_all(workspace):
    """Raises ValueError when search string appears multiple times without replace_all=True."""
    (workspace / "repeat.txt").write_text("foo\nfoo\nbar\n")

    with pytest.raises(ValueError, match="Found 2 occurrences"):
        await edit_file(FakeCtx(), path="repeat.txt", search="foo", replacement="baz")


@pytest.mark.asyncio
async def test_edit_file_replace_all(workspace):
    """Replaces all occurrences when replace_all=True."""
    (workspace / "multi.txt").write_text("foo\nfoo\nbar\n")

    result = await edit_file(
        FakeCtx(), path="multi.txt", search="foo", replacement="qux", replace_all=True
    )

    assert not result.get("error")
    assert result["replacements"] == 2
    assert (workspace / "multi.txt").read_text() == "qux\nqux\nbar\n"


@pytest.mark.asyncio
async def test_edit_file_not_found_path(workspace):
    """Returns error dict when the target file does not exist."""
    result = await edit_file(FakeCtx(), path="ghost.txt", search="x", replacement="y")

    assert result.get("error") is True
