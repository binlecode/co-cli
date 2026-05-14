"""Behavioural tests for skill usage tracking sidecar."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.agents.core import build_tool_registry
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills import usage as skill_usage
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.skills import skill_manage, skill_view

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_CONTENT = """\
---
description: A skill for usage tracking tests
---

Do the test task.
"""

_URL_INSTALLED_CONTENT = """\
---
description: A skill installed from a URL
source-url: https://example.com/skill.md
---

Do the URL-installed task.
"""


def _make_deps(tmp_path: Path, config=SETTINGS) -> CoDeps:
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, config, user_skills_dir=tmp_path)
    tool_registry = build_tool_registry(config)
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_index=dict(tool_registry.tool_index),
        session=CoSessionState(),
        skill_commands=skill_commands,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="skill_manage")


# ---------------------------------------------------------------------------
# read_records / write_records
# ---------------------------------------------------------------------------


def test_read_records_returns_empty_when_sidecar_missing(tmp_path: Path) -> None:
    """read_records returns the empty schema when no sidecar exists."""
    deps = _make_deps(tmp_path)
    records = skill_usage.read_records(deps)
    assert records == {"version": 1, "skills": {}}
    assert not (tmp_path / ".usage.json").exists()


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    """write_records then read_records returns the same data."""
    deps = _make_deps(tmp_path)
    data = {
        "version": 1,
        "skills": {"foo": {"use_count": 3, "pinned": True, "state": "active"}},
    }
    skill_usage.write_records(deps, data)
    assert (tmp_path / ".usage.json").exists()
    assert skill_usage.read_records(deps) == data


def test_read_records_returns_empty_on_corrupt_json(tmp_path: Path) -> None:
    """Corrupt sidecar yields the empty schema (best-effort recovery)."""
    deps = _make_deps(tmp_path)
    (tmp_path / ".usage.json").write_text("{this is not json", encoding="utf-8")
    assert skill_usage.read_records(deps) == {"version": 1, "skills": {}}


def test_write_records_is_atomic(tmp_path: Path) -> None:
    """write_records uses a temp file with os.replace; no .tmp files left behind."""
    deps = _make_deps(tmp_path)
    skill_usage.write_records(deps, {"version": 1, "skills": {}})
    leftover = list(tmp_path.glob(".usage.json.tmp.*"))
    assert leftover == [], f"unexpected tmp leftovers: {leftover}"


# ---------------------------------------------------------------------------
# is_agent_created
# ---------------------------------------------------------------------------


def test_is_agent_created_true_for_user_skill_without_source_url(tmp_path: Path) -> None:
    """A skill file in user_skills_dir without source-url is agent-created."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("my-skill", deps) is True


def test_is_agent_created_false_for_url_installed(tmp_path: Path) -> None:
    """A skill with source-url is upstream-managed even when in user_skills_dir."""
    (tmp_path / "url-skill.md").write_text(_URL_INSTALLED_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("url-skill", deps) is False


def test_is_agent_created_false_for_bundled_only(tmp_path: Path) -> None:
    """A bundled skill (no user copy) is not agent-created."""
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("doctor", deps) is False


def test_is_agent_created_false_for_nonexistent(tmp_path: Path) -> None:
    """Unknown name returns False."""
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("nope-not-real", deps) is False


# ---------------------------------------------------------------------------
# bump_view / bump_use / bump_patch
# ---------------------------------------------------------------------------


def test_bump_view_creates_record_and_increments(tmp_path: Path) -> None:
    """bump_view on an agent-created skill creates a record and increments view_count."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "my-skill")

    records = skill_usage.read_records(deps)
    record = records["skills"]["my-skill"]
    assert record["view_count"] == 1
    assert record["last_viewed_at"] is not None
    assert record["state"] == "active"
    assert record["pinned"] is False
    assert record["use_count"] == 0


def test_bump_view_repeated_increments_counter(tmp_path: Path) -> None:
    """Repeated bump_view calls increment view_count monotonically."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    for _ in range(3):
        skill_usage.bump_view(deps, "my-skill")
    assert skill_usage.read_records(deps)["skills"]["my-skill"]["view_count"] == 3


def test_bump_view_skips_bundled_skill(tmp_path: Path) -> None:
    """bump_view on a bundled-only skill is a no-op (no sidecar entry)."""
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "doctor")
    assert "doctor" not in skill_usage.read_records(deps).get("skills", {})


def test_bump_view_skips_url_installed_skill(tmp_path: Path) -> None:
    """bump_view on a URL-installed skill is a no-op (upstream-managed)."""
    (tmp_path / "url-skill.md").write_text(_URL_INSTALLED_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "url-skill")
    assert "url-skill" not in skill_usage.read_records(deps).get("skills", {})


def test_bump_use_increments_use_count_and_timestamp(tmp_path: Path) -> None:
    """bump_use updates use_count and last_used_at; leaves view_count untouched."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_use(deps, "my-skill")
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["use_count"] == 1
    assert record["view_count"] == 0
    assert record["last_used_at"] is not None
    assert record["last_viewed_at"] is None


def test_bump_patch_increments_patch_count_and_timestamp(tmp_path: Path) -> None:
    """bump_patch updates patch_count and last_patched_at."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_patch(deps, "my-skill")
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["patch_count"] == 1
    assert record["last_patched_at"] is not None


# ---------------------------------------------------------------------------
# record_create / forget / set_pinned
# ---------------------------------------------------------------------------


def test_record_create_initializes_record(tmp_path: Path) -> None:
    """record_create writes a fresh record with all counts at 0 and pinned=False."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.record_create(deps, "my-skill")
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["use_count"] == 0
    assert record["view_count"] == 0
    assert record["patch_count"] == 0
    assert record["created_at"] is not None
    assert record["state"] == "active"
    assert record["pinned"] is False


def test_record_create_skips_url_installed(tmp_path: Path) -> None:
    """record_create on a URL-installed skill is a no-op."""
    (tmp_path / "url-skill.md").write_text(_URL_INSTALLED_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.record_create(deps, "url-skill")
    assert "url-skill" not in skill_usage.read_records(deps).get("skills", {})


def test_forget_removes_entry(tmp_path: Path) -> None:
    """forget removes a skill's record."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.record_create(deps, "my-skill")
    assert "my-skill" in skill_usage.read_records(deps)["skills"]
    skill_usage.forget(deps, "my-skill")
    assert "my-skill" not in skill_usage.read_records(deps).get("skills", {})


def test_forget_unknown_skill_is_noop(tmp_path: Path) -> None:
    """forget on a never-recorded skill doesn't error."""
    deps = _make_deps(tmp_path)
    skill_usage.forget(deps, "nonexistent")
    assert skill_usage.read_records(deps) == {"version": 1, "skills": {}}


def test_set_pinned_creates_stub_when_no_record(tmp_path: Path) -> None:
    """set_pinned on a skill without an existing record creates a stub with pinned=True."""
    deps = _make_deps(tmp_path)
    skill_usage.set_pinned(deps, "ghost-skill", True)
    record = skill_usage.read_records(deps)["skills"]["ghost-skill"]
    assert record["pinned"] is True
    assert record["use_count"] == 0
    assert record["created_at"] is not None


def test_set_pinned_toggles_existing_record(tmp_path: Path) -> None:
    """set_pinned True then False toggles the flag on an existing record."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "my-skill")
    skill_usage.set_pinned(deps, "my-skill", True)
    assert skill_usage.read_records(deps)["skills"]["my-skill"]["pinned"] is True
    skill_usage.set_pinned(deps, "my-skill", False)
    assert skill_usage.read_records(deps)["skills"]["my-skill"]["pinned"] is False


# ---------------------------------------------------------------------------
# usage_tracking_enabled=False short-circuit
# ---------------------------------------------------------------------------


def test_bump_view_short_circuits_when_disabled(tmp_path: Path) -> None:
    """With usage_tracking_enabled=False, bump_view writes nothing."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    config = SETTINGS.model_copy(deep=True)
    config.skills.usage_tracking_enabled = False
    deps = _make_deps(tmp_path, config=config)
    skill_usage.bump_view(deps, "my-skill")
    assert not (tmp_path / ".usage.json").exists()


def test_record_create_short_circuits_when_disabled(tmp_path: Path) -> None:
    """With usage_tracking_enabled=False, record_create writes nothing."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    config = SETTINGS.model_copy(deep=True)
    config.skills.usage_tracking_enabled = False
    deps = _make_deps(tmp_path, config=config)
    skill_usage.record_create(deps, "my-skill")
    assert not (tmp_path / ".usage.json").exists()


# ---------------------------------------------------------------------------
# best-effort error swallowing
# ---------------------------------------------------------------------------


def test_bump_view_swallows_write_failures(tmp_path: Path) -> None:
    """A real OS write failure during bump_view is swallowed; sidecar state stays intact."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    sidecar_path = tmp_path / ".usage.json"
    sidecar_path.mkdir()
    deps = _make_deps(tmp_path)

    skill_usage.bump_view(deps, "my-skill")

    assert sidecar_path.is_dir()
    leftovers = list(tmp_path.glob(".usage.json.tmp.*"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# integration via skill_manage / skill_view tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_manage_create_then_view_produces_sidecar_entry(tmp_path: Path) -> None:
    """skill_manage(create=...) initializes a record; subsequent skill_view bumps view_count."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["created_at"] is not None
    assert record["view_count"] == 0

    await skill_view(ctx, name="my-skill")
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["view_count"] == 1
    assert record["last_viewed_at"] is not None


@pytest.mark.asyncio
async def test_skill_view_on_bundled_does_not_create_sidecar_entry(tmp_path: Path) -> None:
    """skill_view on a bundled skill does not populate the sidecar."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_view(ctx, name="doctor")
    records = skill_usage.read_records(deps)
    assert "doctor" not in records.get("skills", {})


@pytest.mark.asyncio
async def test_skill_manage_patch_bumps_patch_count(tmp_path: Path) -> None:
    """skill_manage(patch=...) increments patch_count in the sidecar."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    await skill_manage(
        ctx,
        action="patch",
        name="my-skill",
        old_string="Do the test task.",
        new_string="Do the patched task.",
    )
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["patch_count"] == 1
    assert record["last_patched_at"] is not None


@pytest.mark.asyncio
async def test_skill_manage_edit_bumps_patch_count(tmp_path: Path) -> None:
    """skill_manage(edit=...) increments patch_count (edit is full-body patch)."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    new_content = "---\ndescription: Edited skill\n---\n\nEdited body.\n"
    await skill_manage(ctx, action="edit", name="my-skill", content=new_content)
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["patch_count"] == 1


@pytest.mark.asyncio
async def test_skill_manage_delete_removes_sidecar_entry(tmp_path: Path) -> None:
    """skill_manage(delete=...) removes the skill's sidecar entry."""
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    assert "my-skill" in skill_usage.read_records(deps)["skills"]

    await skill_manage(ctx, action="delete", name="my-skill")
    assert "my-skill" not in skill_usage.read_records(deps).get("skills", {})


@pytest.mark.asyncio
async def test_install_from_local_path_records_create(tmp_path: Path) -> None:
    """Installing a local-path skill (no source-url) records a create entry."""
    source = tmp_path / "src" / "fresh-install.md"
    source.parent.mkdir()
    source.write_text(_VALID_CONTENT, encoding="utf-8")
    install_dir = tmp_path / "user-skills"
    install_dir.mkdir()
    deps = _make_deps(install_dir)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="install", source=str(source))
    record = skill_usage.read_records(deps)["skills"]["fresh-install"]
    assert record["created_at"] is not None


def test_sidecar_file_format_matches_spec(tmp_path: Path) -> None:
    """Sidecar file structure conforms to plan §High-Level Design."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "my-skill")
    raw = (tmp_path / ".usage.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["version"] == 1
    assert set(data["skills"]["my-skill"].keys()) == {
        "use_count",
        "view_count",
        "patch_count",
        "created_at",
        "last_used_at",
        "last_viewed_at",
        "last_patched_at",
        "state",
        "pinned",
    }
