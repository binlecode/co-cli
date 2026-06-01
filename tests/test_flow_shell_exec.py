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


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


@pytest.mark.asyncio
async def test_shell_exec_runs_in_workspace_dir(tmp_path: Path) -> None:
    """With no workdir, commands run in deps.workspace_dir — not the launch cwd."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="pwd")

    assert not (result.metadata and result.metadata.get("error"))
    assert Path(result.return_value.strip()).resolve() == tmp_path.resolve()


@pytest.mark.asyncio
async def test_shell_exec_workdir_scopes_to_subdir(tmp_path: Path) -> None:
    """A relative workdir anchors under the workspace dir."""
    sub = tmp_path / "sub"
    sub.mkdir()
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="pwd", workdir="sub")

    assert not (result.metadata and result.metadata.get("error"))
    assert Path(result.return_value.strip()).resolve() == sub.resolve()


@pytest.mark.asyncio
async def test_shell_exec_workdir_escape_rejected(tmp_path: Path) -> None:
    """A workdir resolving outside the workspace is rejected (BC-1, no escape)."""
    deps = _make_deps(tmp_path)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await shell_exec(ctx, cmd="pwd", workdir="../..")

    assert result.metadata is not None
    assert result.metadata.get("error") is True
