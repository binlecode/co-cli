"""Behavioural tests for the /filescope slash command.

Covers:
  - Output lists every resolved read root plus the write anchor
  - A read root that does not exist on disk is flagged (missing)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.commands.filescope import _cmd_filescope
from co_cli.commands.types import CommandContext
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.core import console
from co_cli.display.headless import HeadlessFrontend
from co_cli.tools.shell_backend import ShellBackend


def _make_ctx(workspace_dir: Path, file_search_roots: list[Path]) -> CommandContext:
    _, tool_catalog = build_native_toolset()
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
        workspace_dir=workspace_dir,
        file_search_roots=file_search_roots,
    )
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=None,  # type: ignore[arg-type]  # unused by this handler
        frontend=HeadlessFrontend(),
        completer=None,
    )


@pytest.mark.asyncio
async def test_lists_all_roots_and_write_anchor(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    vault = tmp_path / "vault"
    workspace.mkdir()
    vault.mkdir()
    ctx = _make_ctx(workspace, [workspace, vault])

    with console.capture() as cap:
        await _cmd_filescope(ctx, "")
    out = cap.get()

    assert str(workspace) in out
    assert str(vault) in out


@pytest.mark.asyncio
async def test_missing_root_is_flagged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing = tmp_path / "does-not-exist"
    ctx = _make_ctx(workspace, [workspace, missing])

    with console.capture() as cap:
        await _cmd_filescope(ctx, "")
    out = cap.get()

    assert str(missing) in out
    assert "(missing)" in out
