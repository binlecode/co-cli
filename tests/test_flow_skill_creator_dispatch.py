"""Dispatch tests for the /skill-creator bundled workflow skill."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, DelegateToAgent
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")


def _make_ctx(tmp_path: Path) -> CommandContext:
    skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    _, tool_catalog = build_native_toolset()
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
        skill_catalog=skill_catalog,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )
    # agent is not accessed during skill dispatch — only deps.skill_catalog is used
    return CommandContext(message_history=[], deps=deps)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_skill_creator_dispatch_returns_delegate(tmp_path: Path) -> None:
    """/skill-creator dispatches to DelegateToAgent with skill_name='skill-creator'."""
    ctx = _make_ctx(tmp_path)
    outcome = await dispatch("/skill-creator review", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert outcome.skill_name == "skill-creator"
