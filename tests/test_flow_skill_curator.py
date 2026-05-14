"""Behavioral tests for the skill curator agent (maybe_run_curator)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP


def _make_deps(tmp_path: Path):
    """Real CoDeps with user_skills_dir in tmp_path."""
    from co_cli.deps import CoDeps
    from co_cli.llm.factory import build_model
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"skills": SETTINGS_NO_MCP.skills.model_copy(update={"curator_enabled": True})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config, model=build_model(SETTINGS_NO_MCP.llm))
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
    return deps


@pytest.mark.asyncio
async def test_maybe_run_curator_disabled_by_config(tmp_path: Path) -> None:
    """maybe_run_curator returns immediately when curator_enabled=False."""
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"skills": SETTINGS_NO_MCP.skills.model_copy(update={"curator_enabled": False})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config)
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir()

    from co_cli.agents.skill_curator import maybe_run_curator

    await maybe_run_curator(deps)


@pytest.mark.asyncio
async def test_maybe_run_curator_idle_gate(tmp_path: Path) -> None:
    """maybe_run_curator skips when idle time is below threshold."""
    from co_cli.agents.skill_curator import maybe_run_curator

    deps = _make_deps(tmp_path)
    deps.session.last_user_input_at = datetime.now(UTC)

    await maybe_run_curator(deps, bypass_time_gate=True)

    from co_cli.skills.curator import read_curator_state

    state = read_curator_state(deps)
    assert state.get("last_run_at") is None
