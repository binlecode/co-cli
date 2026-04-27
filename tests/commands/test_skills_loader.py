"""Functional tests for skill loading, reload, dispatch, and upgrade flows."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.completion import WordCompleter

from co_cli.agent._core import build_agent
from co_cli.commands._commands import (
    CommandContext,
    DelegateToAgent,
    LocalOnly,
    ReplaceTranscript,
    dispatch,
)
from co_cli.config._core import settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._core import console
from co_cli.skills._skill_types import SkillConfig
from co_cli.tools.shell_backend import ShellBackend


def _write_skill(skills_dir: Path, name: str, content: str) -> Path:
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _make_ctx(
    tmp_path: Path, *, skills_dir: Path | None = None, user_skills_dir: Path | None = None
) -> CommandContext:
    agent = build_agent(config=settings)
    deps = CoDeps(
        shell=ShellBackend(),
        config=settings,
        skills_dir=skills_dir or (tmp_path / "bundled-skills"),
        user_skills_dir=user_skills_dir or (tmp_path / "user-skills"),
        session=CoSessionState(),
    )
    return CommandContext(
        message_history=[],
        deps=deps,
        agent=agent,
    )


@pytest.mark.asyncio
async def test_skills_reload_then_dispatch_substitutes_arguments(tmp_path: Path):
    """Reloaded project skills delegate through dispatch with argument substitution."""
    skills_dir = tmp_path / "bundled-skills"
    _write_skill(skills_dir, "search", "Search for: $ARGUMENTS")

    ctx = _make_ctx(tmp_path, skills_dir=skills_dir)
    reload_result = await dispatch("/skills reload", ctx)
    delegate_result = await dispatch("/search foo bar", ctx)

    assert isinstance(reload_result, LocalOnly)
    assert isinstance(delegate_result, DelegateToAgent)
    assert delegate_result.delegated_input == "Search for: foo bar"


@pytest.mark.asyncio
async def test_skills_reload_updates_registry_and_completer(tmp_path: Path):
    """Reload refreshes both the session skill registry and the live completer words."""
    skills_dir = tmp_path / "bundled-skills"
    _write_skill(
        skills_dir,
        "reload-completer-skill",
        "---\ndescription: Reload test skill\n---\nDo the thing.",
    )

    ctx = _make_ctx(tmp_path, skills_dir=skills_dir)
    ctx.completer = WordCompleter(words=["/help"])

    result = await dispatch("/skills reload", ctx)

    assert isinstance(result, LocalOnly)
    assert "reload-completer-skill" in ctx.deps.skill_commands
    assert "/reload-completer-skill" in ctx.completer.words


@pytest.mark.asyncio
async def test_skills_check_reports_missing_env_requirement(tmp_path: Path):
    """/skills check explains why a skill was skipped instead of failing silently."""
    skills_dir = tmp_path / "bundled-skills"
    _write_skill(
        skills_dir,
        "needs-env",
        "---\nrequires:\n  env:\n    - SOME_NONEXISTENT_VAR_XYZ\n---\nbody",
    )

    ctx = _make_ctx(tmp_path, skills_dir=skills_dir)
    with console.capture() as cap:
        result = await dispatch("/skills check", ctx)
    output = cap.get()

    assert isinstance(result, LocalOnly)
    assert "needs-env.md" in output
    assert "Skipped" in output
    assert "missing env vars" in output


@pytest.mark.asyncio
async def test_skills_install_local_registers_skill(tmp_path: Path):
    """/skills install <path> copies the file to user_skills_dir and registers it in-session."""
    source = tmp_path / "myinstallskill.md"
    source.write_text("---\ndescription: My installed skill\n---\nDo something.", encoding="utf-8")

    user_skills_dir = tmp_path / "user-skills"
    ctx = _make_ctx(tmp_path, user_skills_dir=user_skills_dir)

    result = await dispatch(f"/skills install {source}", ctx)

    assert isinstance(result, LocalOnly)
    assert (user_skills_dir / "myinstallskill.md").exists()
    assert "myinstallskill" in ctx.deps.skill_commands


@pytest.mark.asyncio
async def test_skills_install_url_error(tmp_path: Path):
    """/skills install with an unreachable URL stays local and fails gracefully."""
    ctx = _make_ctx(tmp_path)
    ctx.deps.skills_dir = tmp_path / "bundled-skills"

    result = await dispatch("/skills install http://127.0.0.1:1/skill.md", ctx)

    assert isinstance(result, LocalOnly)


@pytest.mark.asyncio
async def test_skill_upgrade_without_source_url_leaves_file_unchanged(tmp_path: Path):
    """/skills upgrade is a no-op when the skill was not installed from a URL."""
    user_skills_dir = tmp_path / "user-skills"
    original_content = "---\ndescription: Test\n---\nbody"
    skill_file = _write_skill(user_skills_dir, "noupgrade", original_content)

    ctx = _make_ctx(tmp_path, user_skills_dir=user_skills_dir)
    await dispatch("/skills reload", ctx)
    result = await dispatch("/skills upgrade noupgrade", ctx)

    assert isinstance(result, LocalOnly)
    assert skill_file.read_text(encoding="utf-8") == original_content


@pytest.mark.asyncio
async def test_dispatch_skill_returns_delegate_to_agent(tmp_path: Path):
    """Registered skills delegate to the agent with the original body."""
    ctx = _make_ctx(tmp_path)
    ctx.deps.skill_commands["test-boundary-skill"] = SkillConfig(
        name="test-boundary-skill",
        body="Do the thing.",
        description="test",
    )

    result = await dispatch("/test-boundary-skill", ctx)

    assert isinstance(result, DelegateToAgent)
    assert result.delegated_input == "Do the thing."


@pytest.mark.asyncio
async def test_dispatch_builtin_takes_precedence_over_same_name_skill(tmp_path: Path):
    """Built-in slash commands must not be shadowed by a user skill of the same name."""
    ctx = _make_ctx(tmp_path)
    ctx.message_history = ["msg"]
    ctx.deps.skill_commands["clear"] = SkillConfig(
        name="clear", body="skill body", description="t"
    )

    result = await dispatch("/clear", ctx)

    assert isinstance(result, ReplaceTranscript)
    assert result.history == []
