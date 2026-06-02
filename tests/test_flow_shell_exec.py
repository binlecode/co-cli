"""Behavioral tests for shell_exec working-directory anchoring.

No LLM — real subprocess execution only. Confirms shell commands run anchored to
the workspace dir (the write/cwd anchor, BC-1), independent of the launch cwd.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell.execute import shell_exec
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(workspace: Path) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        workspace_dir=workspace,
    )


def _ctx(deps: CoDeps, *, approved: bool = False) -> RunContext[CoDeps]:
    return RunContext(
        deps=deps,
        model=None,
        usage=RunUsage(),
        tool_name="shell_exec",
        tool_call_approved=approved,
    )


@pytest.mark.asyncio
async def test_shell_exec_runs_in_workspace_dir(tmp_path: Path) -> None:
    """With no work_dir, commands run in deps.workspace_dir — not the launch cwd."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="pwd")

    assert not (result.metadata and result.metadata.get("error"))
    assert Path(result.return_value.strip()).resolve() == tmp_path.resolve()


@pytest.mark.asyncio
async def test_shell_exec_work_dir_scopes_to_subdir(tmp_path: Path) -> None:
    """A relative work_dir anchors under the workspace dir."""
    sub = tmp_path / "sub"
    sub.mkdir()
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="pwd", work_dir="sub")

    assert not (result.metadata and result.metadata.get("error"))
    assert Path(result.return_value.strip()).resolve() == sub.resolve()


@pytest.mark.asyncio
async def test_shell_exec_work_dir_escape_rejected(tmp_path: Path) -> None:
    """A work_dir resolving outside the workspace is rejected (BC-1, no escape)."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="pwd", work_dir="../..")

    assert result.metadata is not None
    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_shell_exec_grep_no_match_returns_ok_not_error(tmp_path: Path) -> None:
    """grep with no matches exits 1 but ran fine — returned as output, not an error."""
    (tmp_path / "hello.txt").write_text("hello world\n")
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="grep zzz hello.txt")

    assert not (result.metadata and result.metadata.get("error"))
    assert "no matches found" in result.return_value


@pytest.mark.asyncio
async def test_shell_exec_grep_error_exit2_stays_error(tmp_path: Path) -> None:
    """grep exit 2 (e.g. missing file) is a real error — not reclassified as benign."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="grep zzz missing.txt")

    assert result.metadata is not None
    assert result.metadata.get("error") is True
    assert "exit 2" in result.return_value


@pytest.mark.asyncio
async def test_shell_exec_diff_differs_returns_ok_not_error(tmp_path: Path) -> None:
    """diff exit 1 (files differ) is the wanted result, not a failure."""
    (tmp_path / "a.txt").write_text("one\n")
    (tmp_path / "b.txt").write_text("two\n")
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps, approved=True)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="diff a.txt b.txt")

    assert not (result.metadata and result.metadata.get("error"))
    assert "files differ" in result.return_value


@pytest.mark.asyncio
async def test_shell_exec_command_not_found_annotates_exit_127(tmp_path: Path) -> None:
    """A missing binary exits 127 — the error header explains the standard meaning."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps, approved=True)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="nonexistentcmdxyz123")

    assert result.metadata is not None
    assert result.metadata.get("error") is True
    assert "exit 127" in result.return_value
    assert "command not found" in result.return_value
