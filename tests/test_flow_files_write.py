"""Behavioral tests for file_write, file_patch.

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
from co_cli.tools.files.write import file_patch, file_write
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
# file_write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_write_creates_new_file(tmp_path: Path) -> None:
    """file_write creates a new file with the given content."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_write(ctx, path="new.txt", content="hello world\n")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert (tmp_path / "new.txt").read_text() == "hello world\n"


@pytest.mark.asyncio
async def test_file_write_overwrites_existing_file(tmp_path: Path) -> None:
    """file_write replaces an existing file's content entirely."""
    target = tmp_path / "existing.txt"
    target.write_text("old content\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_write(ctx, path="existing.txt", content="new content\n")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert target.read_text() == "new content\n"


@pytest.mark.asyncio
async def test_file_write_creates_parent_directories(tmp_path: Path) -> None:
    """file_write creates intermediate parent directories as needed."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_write(ctx, path="a/b/c/deep.txt", content="deep\n")

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert (tmp_path / "a" / "b" / "c" / "deep.txt").read_text() == "deep\n"


# ---------------------------------------------------------------------------
# file_patch — replace mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_patch_replace_mode_applies_exact_substitution(tmp_path: Path) -> None:
    """file_patch replaces the old_string with new_string in place."""
    target = tmp_path / "code.py"
    target.write_text("def foo():\n    return 1\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)
    path_key = str(target)
    deps.file_tracker.record_read(path_key, target.stat().st_mtime, partial=False)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_patch(
            ctx,
            path="code.py",
            old_string="return 1",
            new_string="return 42",
        )

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "return 42" in target.read_text()
    assert "return 1" not in target.read_text()


@pytest.mark.asyncio
async def test_file_patch_replace_mode_blocks_unread_file(tmp_path: Path) -> None:
    """file_patch raises ModelRetry when the file has not been read first."""
    from pydantic_ai import ModelRetry

    target = tmp_path / "unread.py"
    target.write_text("x = 1\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    with pytest.raises(ModelRetry, match="file_read"):
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            await file_patch(
                ctx,
                path="unread.py",
                old_string="x = 1",
                new_string="x = 2",
            )


@pytest.mark.asyncio
async def test_file_patch_replace_mode_returns_error_when_old_string_not_found(
    tmp_path: Path,
) -> None:
    """file_patch returns tool_error when old_string is absent from the file."""
    target = tmp_path / "src.py"
    target.write_text("y = 99\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)
    path_key = str(target)
    deps.file_tracker.record_read(path_key, target.stat().st_mtime, partial=False)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_patch(
            ctx,
            path="src.py",
            old_string="x = 1",
            new_string="x = 2",
        )

    assert result.metadata is not None
    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_file_patch_deletes_matched_text_with_empty_new_string(tmp_path: Path) -> None:
    """file_patch with new_string="" removes the matched text in place."""
    target = tmp_path / "del.py"
    target.write_text("keep = 1\nremove = 2\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)
    path_key = str(target)
    deps.file_tracker.record_read(path_key, target.stat().st_mtime, partial=False)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_patch(
            ctx,
            path="del.py",
            old_string="remove = 2\n",
            new_string="",
        )

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert target.read_text() == "keep = 1\n"


# ---------------------------------------------------------------------------
# Write scope stays workspace-anchored even when read scope spans extra roots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_write_rejects_path_under_extra_read_root(tmp_path: Path) -> None:
    """file_write rejects a path under a read-only extra root — read scope never widens write (BC-1)."""
    workspace = tmp_path / "ws"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()

    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        workspace_dir=workspace,
        file_search_roots=[workspace.resolve(), vault.resolve()],
    )
    ctx = _ctx(deps)

    target = vault / "should_not_write.txt"
    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_write(ctx, path=str(target.resolve()), content="nope\n")

    assert result.metadata is not None
    assert result.metadata.get("error") is True, result.return_value
    assert not target.exists(), "write into a read-only extra root must not create the file"
