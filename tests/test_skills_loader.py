"""Functional tests for skill loading, reload, dispatch, and upgrade flows."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.completion import WordCompleter

from co_cli.agent import build_agent
from co_cli.commands._commands import (
    CommandContext,
    DelegateToAgent,
    LocalOnly,
    _load_skills,
    dispatch,
)
from co_cli.config._core import settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display._core import console
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
        skills_dir=skills_dir or (tmp_path / ".co-cli" / "skills"),
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
    skills_dir = tmp_path / ".co-cli" / "skills"
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
    skills_dir = tmp_path / ".co-cli" / "skills"
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
    skills_dir = tmp_path / ".co-cli" / "skills"
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
    """/skills install <path> copies the file and registers it in-session."""
    source = tmp_path / "myinstallskill.md"
    source.write_text("---\ndescription: My installed skill\n---\nDo something.", encoding="utf-8")

    skills_dir = tmp_path / ".co-cli" / "skills"
    ctx = _make_ctx(tmp_path, skills_dir=skills_dir)

    result = await dispatch(f"/skills install {source}", ctx)

    assert isinstance(result, LocalOnly)
    assert (skills_dir / "myinstallskill.md").exists()
    assert "myinstallskill" in ctx.deps.skill_commands


@pytest.mark.asyncio
async def test_skill_upgrade_without_source_url_leaves_file_unchanged(tmp_path: Path):
    """/skills upgrade is a no-op when the skill was not installed from a URL."""
    skills_dir = tmp_path / ".co-cli" / "skills"
    original_content = "---\ndescription: Test\n---\nbody"
    skill_file = _write_skill(skills_dir, "noupgrade", original_content)

    ctx = _make_ctx(tmp_path, skills_dir=skills_dir)
    await dispatch("/skills reload", ctx)
    result = await dispatch("/skills upgrade noupgrade", ctx)

    assert isinstance(result, LocalOnly)
    assert skill_file.read_text(encoding="utf-8") == original_content


def test_load_skills_project_overrides_user_global(tmp_path: Path):
    """Project-local skills override same-name user-global skills."""
    user_skills_dir = tmp_path / "user-skills"
    project_skills_dir = tmp_path / ".co-cli" / "skills"
    _write_skill(user_skills_dir, "shared-skill", "---\ndescription: User\n---\nUser body")
    _write_skill(
        project_skills_dir, "shared-skill", "---\ndescription: Project\n---\nProject body"
    )

    loaded = _load_skills(project_skills_dir, settings, user_skills_dir=user_skills_dir)

    assert loaded["shared-skill"].description == "Project"
    assert loaded["shared-skill"].body == "Project body"


def test_load_skills_rejects_symlink_outside_root(tmp_path: Path):
    """Symlinks escaping the skills root are rejected."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("---\ndescription: Escaped skill\n---\nEvil.", encoding="utf-8")
    (skills_dir / "escaped.md").symlink_to(outside)

    loaded = _load_skills(skills_dir, settings)

    assert "escaped" not in loaded
