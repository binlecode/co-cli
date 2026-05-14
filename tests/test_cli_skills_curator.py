"""Behavioral tests for /skills curator CLI subcommands."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.display.core import console


def _make_deps(tmp_path: Path):
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"skills": SETTINGS_NO_MCP.skills.model_copy(update={"curator_enabled": True})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config)
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
    return deps


def _make_ctx(deps, message_history=None):
    from co_cli.commands.types import CommandContext

    return CommandContext(message_history=message_history or [], deps=deps, agent=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_curator_status_default(tmp_path: Path) -> None:
    """curator status shows a table with enabled/paused/run_count fields."""
    from co_cli.commands.skills import _cmd_skills_curator

    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_curator(ctx, "status")

    output = cap.get()
    assert "enabled" in output
    assert "last_run_at" in output


@pytest.mark.asyncio
async def test_curator_pause_and_resume(tmp_path: Path) -> None:
    """pause writes paused=True; resume writes paused=False."""
    from co_cli.commands.skills import _cmd_skills_curator
    from co_cli.skills.curator import read_curator_state

    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    with console.capture():
        await _cmd_skills_curator(ctx, "pause")
    assert read_curator_state(deps).get("paused") is True

    with console.capture():
        await _cmd_skills_curator(ctx, "resume")
    assert read_curator_state(deps).get("paused") is False


@pytest.mark.asyncio
async def test_curator_run_blocked_by_idle_gate(tmp_path: Path) -> None:
    """curator run prints idle-gate error when idle < threshold."""
    from co_cli.commands.skills import _cmd_skills_curator

    deps = _make_deps(tmp_path)
    deps.session.last_user_input_at = datetime.now(UTC)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_curator(ctx, "run")

    output = cap.get()
    assert "blocked" in output.lower() or "idle" in output.lower()


@pytest.mark.asyncio
async def test_curator_run_blocked_when_paused(tmp_path: Path) -> None:
    """curator run prints paused error when curator is paused."""
    from co_cli.commands.skills import _cmd_skills_curator
    from co_cli.skills.curator import write_curator_state

    deps = _make_deps(tmp_path)
    write_curator_state(deps, {"version": 1, "paused": True, "run_count": 0})
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_curator(ctx, "run")

    output = cap.get()
    assert "paused" in output.lower()


@pytest.mark.asyncio
async def test_curator_restore_missing_name(tmp_path: Path) -> None:
    """curator restore without a name prints usage."""
    from co_cli.commands.skills import _cmd_skills_curator

    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_curator(ctx, "restore")

    output = cap.get()
    assert "Usage" in output


@pytest.mark.asyncio
async def test_curator_restore_unknown_skill(tmp_path: Path) -> None:
    """curator restore <name> prints error when skill is not in archive."""
    from co_cli.commands.skills import _cmd_skills_curator

    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_curator(ctx, "restore nonexistent-skill")

    output = cap.get()
    assert "failed" in output.lower() or "not found" in output.lower() or "Restore" in output


@pytest.mark.asyncio
async def test_curator_unknown_subcommand(tmp_path: Path) -> None:
    """Unknown curator subcommand prints error and usage."""
    from co_cli.commands.skills import _cmd_skills_curator

    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_curator(ctx, "badcmd")

    output = cap.get()
    assert "Unknown" in output or "Usage" in output
