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
    """file_patch(mode='replace') replaces the old_string with new_string in place."""
    target = tmp_path / "code.py"
    target.write_text("def foo():\n    return 1\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)
    path_key = str(target)
    deps.file_tracker.record_read(path_key, target.stat().st_mtime, partial=False)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_patch(
            ctx,
            mode="replace",
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
    """file_patch(mode='replace') raises ModelRetry when the file has not been read first."""
    from pydantic_ai import ModelRetry

    target = tmp_path / "unread.py"
    target.write_text("x = 1\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    with pytest.raises(ModelRetry, match="file_read"):
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            await file_patch(
                ctx,
                mode="replace",
                path="unread.py",
                old_string="x = 1",
                new_string="x = 2",
            )


@pytest.mark.asyncio
async def test_file_patch_replace_mode_returns_error_when_old_string_not_found(
    tmp_path: Path,
) -> None:
    """file_patch(mode='replace') returns tool_error when old_string is absent from the file."""
    target = tmp_path / "src.py"
    target.write_text("y = 99\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)
    path_key = str(target)
    deps.file_tracker.record_read(path_key, target.stat().st_mtime, partial=False)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_patch(
            ctx,
            mode="replace",
            path="src.py",
            old_string="x = 1",
            new_string="x = 2",
        )

    assert result.metadata is not None
    assert result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# file_patch — V4A patch mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_patch_v4a_mode_updates_file_content(tmp_path: Path) -> None:
    """file_patch(mode='patch') applies a V4A Update File patch to modify a file."""
    target = tmp_path / "app.py"
    target.write_text("VERSION = '1.0'\n")

    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)
    path_key = str(target)
    deps.file_tracker.record_read(path_key, target.stat().st_mtime, partial=False)

    v4a = (
        "*** Begin Patch\n"
        "*** Update File: app.py\n"
        "-VERSION = '1.0'\n"
        "+VERSION = '2.0'\n"
        "*** End Patch\n"
    )

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_patch(ctx, mode="patch", patch=v4a)

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert "2.0" in target.read_text()


@pytest.mark.asyncio
async def test_file_patch_v4a_mode_adds_new_file(tmp_path: Path) -> None:
    """file_patch(mode='patch') with Add File op creates a new file."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    v4a = "*** Begin Patch\n*** Add File: newmod.py\n+# generated\n+x = 1\n*** End Patch\n"

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await file_patch(ctx, mode="patch", patch=v4a)

    assert result.metadata is not None
    assert result.metadata.get("error") is not True
    assert (tmp_path / "newmod.py").exists()
    assert "x = 1" in (tmp_path / "newmod.py").read_text()
