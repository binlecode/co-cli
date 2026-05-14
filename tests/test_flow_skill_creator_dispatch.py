"""Dispatch tests for the /skill-creator bundled workflow skill."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.agents.core import build_tool_registry
from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext, DelegateToAgent
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")


def _make_ctx(tmp_path: Path) -> CommandContext:
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, SETTINGS, user_skills_dir=tmp_path)
    tool_registry = build_tool_registry(SETTINGS)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_index=dict(tool_registry.tool_index),
        session=CoSessionState(),
        skill_commands=skill_commands,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )
    # agent is not accessed during skill dispatch — only deps.skill_commands is used
    return CommandContext(message_history=[], deps=deps, agent=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_skill_creator_dispatch_returns_delegate(tmp_path: Path) -> None:
    """/skill-creator dispatches to DelegateToAgent with skill_name='skill-creator'."""
    ctx = _make_ctx(tmp_path)
    outcome = await dispatch("/skill-creator review", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert outcome.skill_name == "skill-creator"


@pytest.mark.asyncio
async def test_skill_creator_body_is_non_empty(tmp_path: Path) -> None:
    """skill-creator dispatch produces a non-empty delegated_input."""
    ctx = _make_ctx(tmp_path)
    outcome = await dispatch("/skill-creator some-task", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert len(outcome.delegated_input) > 0


@pytest.mark.asyncio
async def test_skill_creator_body_references_skill_manage_create(tmp_path: Path) -> None:
    """skill-creator body must reference skill_manage(action='create') — core write call."""
    ctx = _make_ctx(tmp_path)
    outcome = await dispatch("/skill-creator deploy", ctx)
    assert isinstance(outcome, DelegateToAgent)
    assert "skill_manage" in outcome.delegated_input
    assert "create" in outcome.delegated_input
