"""Behavioural tests for skill_view tool (hermes-parity read surface)."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.agent.core import build_tool_registry
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills.loader import load_skills
from co_cli.skills.skill_types import SkillConfig
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.skills import skill_view

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")


def _make_deps(tmp_path: Path, extra_skills: dict[str, SkillConfig] | None = None) -> CoDeps:
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, SETTINGS, user_skills_dir=tmp_path)
    if extra_skills:
        skill_commands = {**skill_commands, **extra_skills}
    tool_registry = build_tool_registry(SETTINGS)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_index=dict(tool_registry.tool_index),
        session=CoSessionState(),
        skill_commands=skill_commands,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps, *, tool_name: str | None = None) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=tool_name)


@pytest.mark.asyncio
async def test_skill_view_returns_body_inline(tmp_path: Path) -> None:
    """skill_view returns doctor body verbatim with no spill placeholder (Constraint 7 guard)."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps, tool_name="skill_view")
    result = await skill_view(ctx, name="doctor")
    assert result.metadata.get("error") is None
    assert result.metadata["linked_files"] == {}
    assert result.metadata["name"] == "doctor"
    assert "<persisted-output>" not in result.return_value
    assert len(result.return_value) > 0
    # Verify body matches what's loaded from disk
    loaded = load_skills(_BUNDLED_SKILLS_DIR, SETTINGS)
    assert result.return_value == loaded["doctor"].body


@pytest.mark.asyncio
async def test_skill_view_body_not_spilled_when_large(tmp_path: Path) -> None:
    """skill_view with spill_threshold_chars=inf never produces a <persisted-output> tag."""
    large_body = "x" * 8000
    big_skill = SkillConfig(name="big-skill", description="large body skill", body=large_body)
    deps = _make_deps(tmp_path, extra_skills={"big-skill": big_skill})
    ctx = _make_ctx(deps, tool_name="skill_view")
    result = await skill_view(ctx, name="big-skill")
    assert "<persisted-output>" not in result.return_value
    assert result.return_value == large_body


@pytest.mark.asyncio
async def test_skill_view_plugin_qualified_name(tmp_path: Path) -> None:
    """skill_view with plugin-qualified name 'plugin:doctor' resolves to doctor body."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps, tool_name="skill_view")
    result = await skill_view(ctx, name="anyplugin:doctor")
    assert result.metadata.get("error") is None
    assert result.metadata["name"] == "doctor"


@pytest.mark.asyncio
async def test_skill_view_unknown_name(tmp_path: Path) -> None:
    """skill_view for an unknown skill name returns tool_error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_view(ctx, name="nonexistent-skill-xyz")
    assert result.metadata is not None
    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_skill_view_blocked_skill(tmp_path: Path) -> None:
    """skill_view for a disable_model_invocation=True skill returns tool_error."""
    blocked = SkillConfig(
        name="blocked",
        description="internal skill",
        body="secret content",
        disable_model_invocation=True,
    )
    deps = _make_deps(tmp_path, extra_skills={"blocked": blocked})
    ctx = _make_ctx(deps)
    result = await skill_view(ctx, name="blocked")
    assert result.metadata is not None
    assert result.metadata.get("error") is True
    assert "not model-invocable" in result.return_value


@pytest.mark.asyncio
async def test_skill_view_file_path_unsupported(tmp_path: Path) -> None:
    """skill_view with file_path returns tool_error (Constraint 3 flat-file degeneracy)."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_view(ctx, name="doctor", file_path="references/x.md")
    assert result.metadata is not None
    assert result.metadata.get("error") is True
    assert "has no linked files" in result.return_value
