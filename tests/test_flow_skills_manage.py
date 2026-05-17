"""Behavioural tests for skill_manage and skill_view tools (hermes-parity write + read surface)."""

import json
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills.loader import load_skills
from co_cli.skills.skill_types import SkillInfo
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.skills import skill_manage, skill_view

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_CONTENT = """\
---
description: A test skill for unit tests
---

Do the test task.
"""

_DESTRUCTIVE_CONTENT = """\
---
description: A skill with destructive shell commands
---

Run this: rm -rf / to clean up everything.
"""


def _make_deps(tmp_path: Path, extra_skills: dict[str, SkillInfo] | None = None) -> CoDeps:
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    if extra_skills:
        skill_index = {**skill_index, **extra_skills}
    _, tool_index = build_native_toolset(SETTINGS)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index=skill_index,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps, *, tool_name: str | None = None) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=tool_name)


def _is_error(result) -> bool:
    return result.metadata is not None and result.metadata.get("error") is True


def _success_data(result) -> dict:
    return json.loads(result.return_value)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_writes_file(tmp_path: Path) -> None:
    """create writes skill file at expected path with matching content."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    assert not _is_error(result)
    data = _success_data(result)
    assert data["success"] is True
    skill_path = tmp_path / "my-skill.md"
    assert skill_path.exists()
    assert skill_path.read_text(encoding="utf-8") == _VALID_CONTENT


@pytest.mark.asyncio
async def test_create_reload(tmp_path: Path) -> None:
    """After create, the new skill appears in deps.skill_index."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    assert "my-skill" in deps.skill_index
    assert deps.skill_index["my-skill"].description == "A test skill for unit tests"


@pytest.mark.asyncio
async def test_create_rejects_empty_description(tmp_path: Path) -> None:
    """create with missing description in frontmatter returns tool_error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    no_desc = "---\n---\n\nBody without description.\n"
    result = await skill_manage(ctx, action="create", name="bad-skill", content=no_desc)
    assert _is_error(result)
    assert "description" in result.return_value
    assert not (tmp_path / "bad-skill.md").exists()


@pytest.mark.asyncio
async def test_create_rolls_back_on_destructive_shell(tmp_path: Path) -> None:
    """create with destructive shell pattern removes the written file and returns tool_error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_manage(
        ctx, action="create", name="bad-skill", content=_DESTRUCTIVE_CONTENT
    )
    assert _is_error(result)
    assert "destructive_shell" in result.return_value
    assert not (tmp_path / "bad-skill.md").exists()
    assert "bad-skill" not in deps.skill_index


@pytest.mark.asyncio
async def test_create_rejects_existing_skill(tmp_path: Path) -> None:
    """create returns tool_error when skill already exists in user dir (no overwrite)."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    result = await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    assert _is_error(result)
    assert "already exists" in result.return_value


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_rewrites_skill(tmp_path: Path) -> None:
    """edit replaces a user-installed skill's full content and reloads."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    new_content = "---\ndescription: Updated description\n---\n\nNew body.\n"
    result = await skill_manage(ctx, action="edit", name="my-skill", content=new_content)
    assert not _is_error(result)
    assert (tmp_path / "my-skill.md").read_text(encoding="utf-8") == new_content
    assert deps.skill_index["my-skill"].description == "Updated description"


@pytest.mark.asyncio
async def test_edit_bundled_only_skill_fails(tmp_path: Path) -> None:
    """edit of a bundled-only skill returns 'copy first' error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    # "doctor" is bundled — no copy in user dir
    result = await skill_manage(ctx, action="edit", name="doctor", content=_VALID_CONTENT)
    assert _is_error(result)
    assert "not found in user skills dir" in result.return_value


@pytest.mark.asyncio
async def test_edit_rollback_restores_original_on_security_flag(tmp_path: Path) -> None:
    """edit with a security-flagged new body restores the original content."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    result = await skill_manage(ctx, action="edit", name="my-skill", content=_DESTRUCTIVE_CONTENT)
    assert _is_error(result)
    assert (tmp_path / "my-skill.md").read_text(encoding="utf-8") == _VALID_CONTENT
    assert deps.skill_index["my-skill"].description == "A test skill for unit tests"


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_unique_match_replaces_and_reloads(tmp_path: Path) -> None:
    """patch with a unique old_string replaces it and reloads."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    result = await skill_manage(
        ctx,
        action="patch",
        name="my-skill",
        old_string="Do the test task.",
        new_string="Do the patched task.",
    )
    assert not _is_error(result)
    assert "patched task" in (tmp_path / "my-skill.md").read_text(encoding="utf-8")
    assert "patched task" in deps.skill_index["my-skill"].body


@pytest.mark.asyncio
async def test_patch_multiple_matches_replace_all_false_errors(tmp_path: Path) -> None:
    """patch with multiple matches and replace_all=False returns error with match count."""
    content = "---\ndescription: Multi-match skill\n---\n\nfoo foo foo\n"
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="multi", content=content)
    result = await skill_manage(
        ctx, action="patch", name="multi", old_string="foo", new_string="bar", replace_all=False
    )
    assert _is_error(result)
    assert "3" in result.return_value


@pytest.mark.asyncio
async def test_patch_replace_all_replaces_all_occurrences(tmp_path: Path) -> None:
    """patch with replace_all=True replaces every occurrence."""
    content = "---\ndescription: Replace all skill\n---\n\nfoo foo foo\n"
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="multi", content=content)
    result = await skill_manage(
        ctx, action="patch", name="multi", old_string="foo", new_string="bar", replace_all=True
    )
    assert not _is_error(result)
    body = (tmp_path / "multi.md").read_text(encoding="utf-8")
    assert "foo" not in body
    assert body.count("bar") == 3


@pytest.mark.asyncio
async def test_patch_zero_matches_errors(tmp_path: Path) -> None:
    """patch with old_string not found returns tool_error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    result = await skill_manage(
        ctx, action="patch", name="my-skill", old_string="NONEXISTENT_STRING_XYZ", new_string="x"
    )
    assert _is_error(result)
    assert "0 matches" in result.return_value


@pytest.mark.asyncio
async def test_patch_security_flag_rollback(tmp_path: Path) -> None:
    """patch that produces a security-flagged result rolls back to original."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    result = await skill_manage(
        ctx,
        action="patch",
        name="my-skill",
        old_string="Do the test task.",
        new_string="Run this: rm -rf / to clean everything.",
    )
    assert _is_error(result)
    assert (tmp_path / "my-skill.md").read_text(encoding="utf-8") == _VALID_CONTENT


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_file_and_promotes_bundled_shadow(tmp_path: Path) -> None:
    """delete removes user copy; bundled skill with same name becomes active again."""
    # Place a user copy of "doctor" that shadows the bundled one
    bundled_body = load_skills(_BUNDLED_SKILLS_DIR)["doctor"].body
    user_doctor_content = "---\ndescription: User override of doctor\n---\n\nCustom doctor.\n"
    (tmp_path / "doctor.md").write_text(user_doctor_content, encoding="utf-8")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    # User copy should be active
    assert deps.skill_index["doctor"].description == "User override of doctor"

    result = await skill_manage(ctx, action="delete", name="doctor")

    assert not _is_error(result)
    data = _success_data(result)
    assert data["shadowed_bundled"] is True
    assert not (tmp_path / "doctor.md").exists()
    # Bundled copy should be active after reload
    assert deps.skill_index["doctor"].body == bundled_body


@pytest.mark.asyncio
async def test_delete_unknown_name_errors(tmp_path: Path) -> None:
    """delete of a skill that doesn't exist in user dir returns tool_error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_manage(ctx, action="delete", name="nonexistent-skill-xyz")
    assert _is_error(result)
    assert "not found" in result.return_value


@pytest.mark.asyncio
async def test_delete_bundled_only_skill_errors(tmp_path: Path) -> None:
    """delete of a bundled-only skill returns 'copy first' error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_manage(ctx, action="delete", name="doctor")
    assert _is_error(result)
    assert "bundled" in result.return_value


# ---------------------------------------------------------------------------
# linked-file stubs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_stub_returns_linked_file_error(tmp_path: Path) -> None:
    """write_file action returns linked-file-deferred error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_manage(ctx, action="write_file", name="my-skill", file_path="x.md")
    assert _is_error(result)
    assert "not yet supported" in result.return_value


@pytest.mark.asyncio
async def test_remove_file_stub_returns_linked_file_error(tmp_path: Path) -> None:
    """remove_file action returns linked-file-deferred error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_manage(ctx, action="remove_file", name="my-skill", file_path="x.md")
    assert _is_error(result)
    assert "not yet supported" in result.return_value


@pytest.mark.asyncio
async def test_patch_with_file_path_returns_linked_file_error(tmp_path: Path) -> None:
    """patch with file_path set returns linked-file-deferred error."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_manage(
        ctx,
        action="patch",
        name="my-skill",
        old_string="x",
        new_string="y",
        file_path="references/x.md",
    )
    assert _is_error(result)
    assert "not yet supported" in result.return_value


# ---------------------------------------------------------------------------
# invalid name validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_name",
    [
        "BadName",
        "name with space",
        "a" * 65,
        "skill!",
        "",
    ],
)
async def test_invalid_name_rejected_before_dispatch(tmp_path: Path, bad_name: str) -> None:
    """Invalid skill names return tool_error before any action dispatch."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_manage(ctx, action="create", name=bad_name, content=_VALID_CONTENT)
    assert _is_error(result)


# ---------------------------------------------------------------------------
# TASK-2: size guardrail — size_warning on create at >= 30 skills
# ---------------------------------------------------------------------------


def _make_deps_with_preloaded_skills(tmp_path: Path, *, extra_user_skill_count: int) -> CoDeps:
    """Return deps with user_skills_dir pre-populated with extra_user_skill_count skill files."""
    for i in range(extra_user_skill_count):
        skill_name = f"prefill-skill-{i:03d}"
        (tmp_path / f"{skill_name}.md").write_text(
            f"---\ndescription: Prefill skill number {i} for size guardrail test\n---\nBody {i}.\n",
            encoding="utf-8",
        )
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    _, tool_index = build_native_toolset(SETTINGS)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index=skill_index,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )


@pytest.mark.asyncio
async def test_create_emits_size_warning_when_count_reaches_30(tmp_path: Path) -> None:
    """skill_manage(action='create') includes size_warning in result when total skill count >= 30.

    Pre-populate user_skills_dir with enough skills so that after the new skill is written
    and deps.skill_index is reloaded, len(deps.skill_index) >= 30.
    Bundled skills directory contributes 6 skills; 24 pre-existing user skills + 1 new = 31 total.

    Failure mode: if size_warning is absent, the model has no signal to prune the skill catalog.
    """
    # 6 bundled + 24 pre-existing + 1 new = 31 >= 30
    deps = _make_deps_with_preloaded_skills(tmp_path, extra_user_skill_count=24)
    ctx = _make_ctx(deps)

    result = await skill_manage(
        ctx,
        action="create",
        name="size-guardrail-skill",
        content="---\ndescription: Skill that triggers the size guardrail\n---\nBody.\n",
    )

    assert not _is_error(result), f"create must succeed; got error: {result.return_value}"
    data = _success_data(result)
    assert data["success"] is True
    assert "size-guardrail-skill" in deps.skill_index
    assert len(deps.skill_index) >= 30, (
        f"Expected >= 30 skills after create, got {len(deps.skill_index)}"
    )
    assert "size_warning" in data, (
        f"size_warning must appear in result when skill count >= 30; got keys: {list(data.keys())}"
    )


@pytest.mark.asyncio
async def test_create_no_size_warning_below_30(tmp_path: Path) -> None:
    """skill_manage(action='create') does not include size_warning when skill count < 30.

    Failure mode: spurious size_warning at low counts is noise and erodes trust in the signal.
    """
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = await skill_manage(
        ctx,
        action="create",
        name="normal-skill",
        content="---\ndescription: A normal skill well below the size threshold\n---\nBody.\n",
    )

    assert not _is_error(result), f"create must succeed; got error: {result.return_value}"
    data = _success_data(result)
    assert data["success"] is True
    assert "size_warning" not in data, f"size_warning must not appear when count < 30; got: {data}"


# ---------------------------------------------------------------------------
# skill_view
# ---------------------------------------------------------------------------


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
    loaded = load_skills(_BUNDLED_SKILLS_DIR)
    assert result.return_value == loaded["doctor"].body


@pytest.mark.asyncio
async def test_skill_view_body_not_spilled_when_large(tmp_path: Path) -> None:
    """skill_view with a large body never produces a <persisted-output> tag."""
    large_body = "x" * 8000
    big_skill = SkillInfo(name="big-skill", description="large body skill", body=large_body)
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
    blocked = SkillInfo(
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
    """skill_view with file_path returns tool_error (flat-file degeneracy guard)."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    result = await skill_view(ctx, name="doctor", file_path="references/x.md")
    assert result.metadata is not None
    assert result.metadata.get("error") is True
    assert "has no linked files" in result.return_value
