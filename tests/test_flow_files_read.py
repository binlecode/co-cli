"""Behavioral tests for file_find, file_read, file_search.

No LLM — real filesystem operations only.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.files.read import file_find, file_read, file_search
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(workspace: Path) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        workspace_dir=workspace,
    )


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


# ---------------------------------------------------------------------------
# file_find
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_find_lists_directory_entries(tmp_path: Path) -> None:
    """file_find returns file and directory entries with their type."""
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "beta.txt").write_text("b")
    sub = tmp_path / "sub"
    sub.mkdir()

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_find(ctx, path=".", pattern="*")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    names = {e["name"] for e in result.metadata.get("entries", [])}
    assert "alpha.txt" in names
    assert "beta.txt" in names
    assert "sub" in names


@pytest.mark.asyncio
async def test_file_find_recursive_pattern_returns_nested_files(tmp_path: Path) -> None:
    """file_find with **/*.py pattern discovers nested .py files."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("x = 1")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_find(ctx, path=".", pattern="**/*.py")

    entries = result.metadata.get("entries", [])
    assert any("mod.py" in e["name"] for e in entries), f"nested .py not found: {entries}"


@pytest.mark.asyncio
async def test_file_find_rejects_path_outside_workspace(tmp_path: Path) -> None:
    """file_find returns tool_error when path escapes the workspace root."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_find(ctx, path="../../etc", pattern="*")

    assert result.metadata is not None
    assert result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_read_returns_line_numbered_content(tmp_path: Path) -> None:
    """file_read returns content with cat-n line numbers."""
    f = tmp_path / "hello.txt"
    f.write_text("line one\nline two\nline three\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="hello.txt")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "1" in result.return_value
    assert "line one" in result.return_value


@pytest.mark.asyncio
async def test_file_read_partial_range_respects_start_end_line(tmp_path: Path) -> None:
    """file_read with start_line/end_line returns only the requested slice."""
    lines = [f"line {i}\n" for i in range(1, 11)]
    f = tmp_path / "big.txt"
    f.write_text("".join(lines))

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="big.txt", start_line=3, end_line=5)

    assert "line 3" in result.return_value
    assert "line 5" in result.return_value
    assert "line 1" not in result.return_value
    assert "line 6" not in result.return_value


@pytest.mark.asyncio
async def test_file_read_not_found_returns_error_with_similar_names(tmp_path: Path) -> None:
    """file_read for a missing file returns tool_error mentioning similar filenames."""
    (tmp_path / "config.py").write_text("x = 1")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="cofig.py")

    assert result.metadata is not None
    assert result.metadata.get("error") is True
    assert "config.py" in result.return_value, "error must suggest similar filename"


@pytest.mark.asyncio
async def test_file_read_rejects_directory_path(tmp_path: Path) -> None:
    """file_read on a directory path returns tool_error."""
    sub = tmp_path / "subdir"
    sub.mkdir()

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path="subdir")

    assert result.metadata is not None
    assert result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# file_search — all output_mode values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_search_content_mode_returns_matching_lines(tmp_path: Path) -> None:
    """file_search with output_mode='content' returns matching lines with file:line: text."""
    (tmp_path / "a.txt").write_text("hello world\ngoodbye\n")
    (tmp_path / "b.txt").write_text("no match here\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, pattern="hello", output_mode="content")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert result.metadata.get("count", 0) >= 1
    assert "hello world" in result.return_value


@pytest.mark.asyncio
async def test_file_search_files_with_matches_mode_returns_paths_only(tmp_path: Path) -> None:
    """file_search with output_mode='files_with_matches' returns only file paths, no line content."""
    (tmp_path / "match.txt").write_text("needle is here\n")
    (tmp_path / "nomatch.txt").write_text("nothing relevant\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, pattern="needle", output_mode="files_with_matches")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "match.txt" in result.return_value
    assert "nomatch.txt" not in result.return_value
    # files_with_matches mode must not include line-number annotations
    assert ":1:" not in result.return_value


@pytest.mark.asyncio
async def test_file_search_count_mode_returns_per_file_counts(tmp_path: Path) -> None:
    """file_search with output_mode='count' returns match counts per file."""
    (tmp_path / "many.txt").write_text("foo\nfoo\nfoo\n")
    (tmp_path / "one.txt").write_text("foo\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, pattern="foo", output_mode="count")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert result.metadata.get("count", 0) >= 4
    # count mode output must include per-file counts
    assert "many.txt" in result.return_value
    assert "3" in result.return_value


@pytest.mark.asyncio
async def test_file_search_invalid_regex_returns_error(tmp_path: Path) -> None:
    """file_search with an invalid regex pattern returns tool_error."""
    (tmp_path / "x.txt").write_text("irrelevant\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, pattern="[unclosed")

    assert result.metadata is not None
    assert result.metadata.get("error") is True
