"""Behavioural tests for skill_manage tool (hermes-parity write surface)."""

import json
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.agents.core import build_tool_registry
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.skills import _skill_manage_approval_subject, skill_manage

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


def _make_deps(tmp_path: Path) -> CoDeps:
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, SETTINGS, user_skills_dir=tmp_path)
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


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="skill_manage")


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
    """After create, the new skill appears in deps.skill_commands."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    assert "my-skill" in deps.skill_commands
    assert deps.skill_commands["my-skill"].description == "A test skill for unit tests"


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
    assert "bad-skill" not in deps.skill_commands


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
    assert deps.skill_commands["my-skill"].description == "Updated description"


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
    assert deps.skill_commands["my-skill"].description == "A test skill for unit tests"


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
    assert "patched task" in deps.skill_commands["my-skill"].body


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
    bundled_body = load_skills(_BUNDLED_SKILLS_DIR, SETTINGS)["doctor"].body
    user_doctor_content = "---\ndescription: User override of doctor\n---\n\nCustom doctor.\n"
    (tmp_path / "doctor.md").write_text(user_doctor_content, encoding="utf-8")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    # User copy should be active
    assert deps.skill_commands["doctor"].description == "User override of doctor"

    result = await skill_manage(ctx, action="delete", name="doctor")

    assert not _is_error(result)
    data = _success_data(result)
    assert data["shadowed_bundled"] is True
    assert not (tmp_path / "doctor.md").exists()
    # Bundled copy should be active after reload
    assert deps.skill_commands["doctor"].body == bundled_body


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
# TASK-2: skill loader hook — index integration tests
# ---------------------------------------------------------------------------


def _make_deps_with_index(tmp_path: Path) -> CoDeps:
    """Like _make_deps but with a real SkillIndex for index hook tests."""
    from tests._settings import SETTINGS

    from co_cli.skills.index import SkillIndex

    index = SkillIndex(
        config=SETTINGS,
        memory_db_path=tmp_path / "search.db",
    )
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, SETTINGS, user_skills_dir=tmp_path)
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
        skill_index=index,
    )


def test_refresh_skills_indexes_bundled_doctor_skill(tmp_path: Path) -> None:
    """refresh_skills populates SkillIndex so doctor skill is discoverable via .search()."""
    from co_cli.skills.lifecycle import refresh_skills

    deps = _make_deps_with_index(tmp_path)
    try:
        refresh_skills(deps)
        hits = deps.skill_index.search("doctor", limit=5)
        assert any(h.name == "doctor" for h in hits), (
            f"Expected 'doctor' in SkillIndex hits, got: {[h.name for h in hits]}"
        )
    finally:
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_create_then_searchable_via_index(tmp_path: Path) -> None:
    """After skill_manage(action='create'), the new skill appears in SkillIndex.search()."""
    deps = _make_deps_with_index(tmp_path)
    ctx = _make_ctx(deps)
    try:
        result = await skill_manage(
            ctx,
            action="create",
            name="my-index-test-skill",
            content="---\ndescription: A unique skill for index verification\n---\nDo the index thing.\n",
        )
        assert not _is_error(result), f"create failed: {result.return_value}"

        hits = deps.skill_index.search("unique skill index verification", limit=5)
        assert any(h.name == "my-index-test-skill" for h in hits), (
            f"Newly created skill must be in index; got: {[h.name for h in hits]}"
        )
    finally:
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_delete_then_not_searchable_via_index(tmp_path: Path) -> None:
    """After skill_manage(action='delete'), the skill is removed from SkillIndex.search()."""
    deps = _make_deps_with_index(tmp_path)
    ctx = _make_ctx(deps)
    try:
        await skill_manage(
            ctx,
            action="create",
            name="to-be-deleted-skill",
            content="---\ndescription: Skill that will be deleted from index\n---\nDo work.\n",
        )
        before = deps.skill_index.search("deleted from index", limit=5)
        assert any(h.name == "to-be-deleted-skill" for h in before), (
            "Skill must be indexed before delete"
        )

        result = await skill_manage(ctx, action="delete", name="to-be-deleted-skill")
        assert not _is_error(result), f"delete failed: {result.return_value}"

        after = deps.skill_index.search("deleted from index", limit=5)
        assert not any(h.name == "to-be-deleted-skill" for h in after), (
            "Deleted skill must be absent from index"
        )
    finally:
        deps.skill_index.close()


# ---------------------------------------------------------------------------
# TASK-5: skill_manage(action='install') — error cases and approval subject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_from_local_path_writes_file(tmp_path: Path) -> None:
    """install from a local .md path writes the file to user_skills_dir and reloads.

    Regression guard: if install doesn't call _atomic_write_skill or _reload_skills,
    the file will be missing or the new skill will not appear in skill_commands.
    """
    source = tmp_path / "source-dir" / "new-install.md"
    source.parent.mkdir()
    source.write_text(
        "---\ndescription: Skill installed from a local file\n---\nDo the installed thing.\n",
        encoding="utf-8",
    )
    install_dir = tmp_path / "user-skills"
    install_dir.mkdir()
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        skill_commands=load_skills(_BUNDLED_SKILLS_DIR, SETTINGS, user_skills_dir=install_dir),
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=install_dir,
    )
    ctx = _make_ctx(deps)

    result = await skill_manage(ctx, action="install", source=str(source))

    assert not _is_error(result), f"install failed: {result.return_value}"
    assert (install_dir / "new-install.md").exists(), "installed file must be in user_skills_dir"
    assert "new-install" in deps.skill_commands, (
        "new skill must appear in skill_commands after reload"
    )


@pytest.mark.asyncio
async def test_install_non_md_source_errors(tmp_path: Path) -> None:
    """install with a non-.md local file returns tool_error.

    Regression guard: if the .md extension check is absent, arbitrary files are
    written into the skills directory and treated as runnable skills.
    """
    source = tmp_path / "not-a-skill.txt"
    source.write_text("just text", encoding="utf-8")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = await skill_manage(ctx, action="install", source=str(source))

    assert _is_error(result), "non-.md source must return tool_error"
    assert ".md" in result.return_value.lower() or "md" in result.return_value.lower(), (
        "error message must mention the .md requirement"
    )


@pytest.mark.asyncio
async def test_install_collision_errors_with_edit_hint(tmp_path: Path) -> None:
    """install of a skill whose name already exists in user_skills_dir returns tool_error.

    The error must direct the caller to use action='edit' instead of silently overwriting.
    Regression guard: if the collision check is absent, installs silently overwrite user edits.
    """
    existing = tmp_path / "existing-skill.md"
    existing.write_text(
        "---\ndescription: Already installed\n---\nExisting body.\n", encoding="utf-8"
    )
    source = tmp_path / "source" / "existing-skill.md"
    source.parent.mkdir()
    source.write_text("---\ndescription: Replacement attempt\n---\nNew body.\n", encoding="utf-8")
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)

    result = await skill_manage(ctx, action="install", source=str(source))

    assert _is_error(result), "collision must return tool_error"
    assert "edit" in result.return_value.lower(), (
        "error message must direct caller to use action='edit'"
    )
    assert existing.read_text(encoding="utf-8").startswith(
        "---\ndescription: Already installed"
    ), "existing file must not be overwritten on collision"


@pytest.mark.asyncio
async def test_install_destructive_content_errors_and_removes_file(tmp_path: Path) -> None:
    """install with security-flagged content returns tool_error and removes the written file.

    Regression guard: if _scan_or_rollback is not called after write, a destructive skill
    is left on disk and will be loaded into skill_commands on next reload.
    """
    source = tmp_path / "source-dir" / "danger-skill.md"
    source.parent.mkdir()
    source.write_text(_DESTRUCTIVE_CONTENT, encoding="utf-8")
    install_dir = tmp_path / "user-skills"
    install_dir.mkdir()
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        skill_commands=load_skills(_BUNDLED_SKILLS_DIR, SETTINGS, user_skills_dir=install_dir),
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=install_dir,
    )
    ctx = _make_ctx(deps)

    result = await skill_manage(ctx, action="install", source=str(source))

    assert _is_error(result), "security-flagged install must return tool_error"
    assert not (install_dir / "danger-skill.md").exists(), (
        "security-flagged skill file must be removed after rollback"
    )


def test_install_approval_subject_url_host() -> None:
    """Approval subject for install from URL must encode the host as tool:skill_manage:install:url:<host>.

    Regression guard: wrong key format breaks session-level approval rules —
    a remembered 'allow github.com skills' approval would not match future installs.
    """
    subject = _skill_manage_approval_subject(
        {"action": "install", "source": "https://github.com/user/skill.md"}
    )

    assert subject.tool_name == "skill_manage"
    assert subject.value == "tool:skill_manage:install:url:github.com"
    assert subject.can_remember is True


def test_install_approval_subject_localfile() -> None:
    """Approval subject for install from local path must use tool:skill_manage:install:localfile.

    Regression guard: URL vs. localfile distinction allows different approval policies
    for network sources vs local filesystem sources.
    """
    subject = _skill_manage_approval_subject(
        {"action": "install", "source": "/home/user/skills/my-skill.md"}
    )

    assert subject.value == "tool:skill_manage:install:localfile"
