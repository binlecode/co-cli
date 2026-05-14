"""Behavioral tests for /skills review CLI subcommand."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.display.core import console


def _make_deps(tmp_path: Path, *, with_model: bool = False):
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"skills": SETTINGS_NO_MCP.skills.model_copy(update={"review_enabled": True})}
    )
    if with_model:
        from co_cli.llm.factory import build_model

        model = build_model(SETTINGS_NO_MCP.llm)
    else:
        model = None
    deps = CoDeps(shell=ShellBackend(), config=config, model=model)
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
    return deps


def _make_ctx(deps, message_history=None):
    from co_cli.commands.types import CommandContext

    return CommandContext(
        message_history=message_history if message_history is not None else [],
        deps=deps,
        agent=None,
    )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_review_run_no_model(tmp_path: Path) -> None:
    """review run prints error when no model is configured."""
    from co_cli.commands.skills import _cmd_skills_review

    deps = _make_deps(tmp_path, with_model=False)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_review(ctx, "run")

    output = cap.get()
    assert "No model" in output or "model" in output.lower()


@pytest.mark.asyncio
async def test_review_unknown_subcommand(tmp_path: Path) -> None:
    """Unknown review subcommand prints usage."""
    from co_cli.commands.skills import _cmd_skills_review

    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_review(ctx, "badcmd")

    output = cap.get()
    assert "Usage" in output
