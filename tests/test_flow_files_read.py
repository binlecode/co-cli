"""Behavioral tests for file_read, file_search.

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
from co_cli.tools.files.read import file_read, file_search
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
# file_search — file listing (content omitted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_search_lists_directory_entries(tmp_path: Path) -> None:
    """file_search with content omitted and a flat glob lists file and dir entries."""
    (tmp_path / "alpha.txt").write_text("a")
    (tmp_path / "beta.txt").write_text("b")
    (tmp_path / "sub").mkdir()

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="*")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "alpha.txt" in result.return_value
    assert "beta.txt" in result.return_value
    assert "sub" in result.return_value


@pytest.mark.asyncio
async def test_file_search_recursive_glob_returns_nested_files(tmp_path: Path) -> None:
    """file_search with a **/*.py path glob discovers nested .py files."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("x = 1")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="**/*.py")

    assert "mod.py" in result.return_value, f"nested .py not found: {result.return_value}"


@pytest.mark.asyncio
async def test_file_search_directory_prefix_scopes_listing(tmp_path: Path) -> None:
    """A path with a directory prefix scopes the listing to that subtree."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("x = 1")
    (tmp_path / "top.py").write_text("y = 2")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="pkg/**/*.py")

    assert "mod.py" in result.return_value
    assert "top.py" not in result.return_value


@pytest.mark.asyncio
async def test_file_search_rejects_path_outside_workspace(tmp_path: Path) -> None:
    """file_search returns tool_error when the path prefix escapes the workspace."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="../../etc/*")

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
# file_search — content search (content given)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_search_content_returns_matching_lines(tmp_path: Path) -> None:
    """file_search with content set returns matching lines with file:line: text."""
    (tmp_path / "a.txt").write_text("hello world\ngoodbye\n")
    (tmp_path / "b.txt").write_text("no match here\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, content="hello")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert result.metadata.get("count", 0) >= 1
    assert "hello world" in result.return_value


@pytest.mark.asyncio
async def test_file_search_files_only_returns_paths_only(tmp_path: Path) -> None:
    """file_search with files_only=True returns only file paths, no line content."""
    (tmp_path / "match.txt").write_text("needle is here\n")
    (tmp_path / "nomatch.txt").write_text("nothing relevant\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, content="needle", files_only=True)

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "match.txt" in result.return_value
    assert "nomatch.txt" not in result.return_value
    # files_only must not include line-number annotations
    assert ":1:" not in result.return_value


@pytest.mark.asyncio
async def test_file_search_content_scoped_by_path_glob(tmp_path: Path) -> None:
    """content search honors the path glob — only matching files are searched."""
    (tmp_path / "keep.py").write_text("needle here\n")
    (tmp_path / "skip.txt").write_text("needle here too\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, path="**/*.py", content="needle")

    assert "keep.py" in result.return_value
    assert "skip.txt" not in result.return_value


@pytest.mark.asyncio
async def test_file_search_invalid_regex_returns_error(tmp_path: Path) -> None:
    """file_search with an invalid content regex returns tool_error."""
    (tmp_path / "x.txt").write_text("irrelevant\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, content="[unclosed")

    assert result.metadata is not None
    assert result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# Multi-root file_search / file_read (file_search_roots)
# ---------------------------------------------------------------------------


def _make_multiroot_deps(workspace: Path, *extra_roots: Path) -> CoDeps:
    """Deps whose read scope spans workspace + extra roots; write anchor stays workspace."""
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        workspace_dir=workspace,
        file_search_roots=[workspace.resolve(), *(r.resolve() for r in extra_roots)],
    )


@pytest.mark.asyncio
async def test_file_read_absolute_path_under_extra_root(tmp_path: Path) -> None:
    """file_read accepts an absolute path under a configured non-workspace root (TASK-3a/3)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("vault content here\n")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path=str(note.resolve()))

    assert result.metadata is not None
    assert result.metadata.get("error") is not True, result.return_value
    assert "vault content here" in result.return_value


@pytest.mark.asyncio
async def test_file_search_content_spans_both_roots(tmp_path: Path) -> None:
    """A broad content search returns hits from workspace AND extra root in one call (TASK-3)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()
    (workspace / "ws_file.txt").write_text("MARKER_TOKEN in workspace\n")
    (vault / "vault_file.txt").write_text("MARKER_TOKEN in vault\n")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_search(ctx, content="MARKER_TOKEN")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True, result.return_value
    assert "ws_file.txt" in result.return_value
    assert "vault_file.txt" in result.return_value
    assert result.metadata.get("count", 0) >= 2


@pytest.mark.asyncio
async def test_multiroot_search_hit_roundtrips_to_file_read(tmp_path: Path) -> None:
    """A multi-root file_search hit (absolute) feeds verbatim to file_read (TASK-4, BC-5)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()
    (vault / "target.md").write_text("ROUNDTRIP_MARKER body\n")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        search = await file_search(ctx, content="ROUNDTRIP_MARKER", files_only=True)

    assert search.metadata is not None
    assert search.metadata.get("error") is not True, search.return_value
    # The printed hit is an absolute path under the vault root; feed it back verbatim.
    hit = search.return_value.strip().splitlines()[0].strip()
    assert hit == str((vault / "target.md").resolve()), hit

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        read = await file_read(ctx, path=hit)

    assert read.metadata is not None
    assert read.metadata.get("error") is not True, read.return_value
    assert "ROUNDTRIP_MARKER body" in read.return_value


@pytest.mark.asyncio
async def test_file_read_rejects_path_outside_all_roots(tmp_path: Path) -> None:
    """An absolute path under no configured root raises a boundary error (BC-2)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    workspace.mkdir()
    vault.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("should be unreachable\n")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path=str(secret.resolve()))

    assert result.metadata is not None
    assert result.metadata.get("error") is True, result.return_value


@pytest.mark.asyncio
async def test_file_read_rejects_in_vault_symlink_escaping_all_roots(tmp_path: Path) -> None:
    """An in-root symlink whose resolved target is under no root is rejected (BC-2)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    workspace.mkdir()
    vault.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("escaped\n")
    link = vault / "escape.txt"
    link.symlink_to(outside / "secret.txt")

    deps = _make_multiroot_deps(workspace, vault)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_read(ctx, path=str(link))

    assert result.metadata is not None
    assert result.metadata.get("error") is True, result.return_value
