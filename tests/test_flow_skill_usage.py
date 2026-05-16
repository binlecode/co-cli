"""Behavioural tests for skill usage tracking sidecar."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.agents.core import build_native_toolset
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


def _make_deps(tmp_path: Path, config=SETTINGS) -> CoDeps:
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    _, tool_index = build_native_toolset(config)
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index=skill_index,
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
    deps = _make_deps(tmp_path)
    assert skill_usage.read_records(deps) == {"version": 1, "skills": {}}
    assert not (tmp_path / ".usage.json").exists()


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    data = {
        "version": 1,
        "skills": {"foo": {"use_count": 3, "pinned": True, "state": "active"}},
    }
    skill_usage.write_records(deps, data)
    assert (tmp_path / ".usage.json").exists()
    assert skill_usage.read_records(deps) == data


def test_read_records_returns_empty_on_corrupt_json(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    (tmp_path / ".usage.json").write_text("{this is not json", encoding="utf-8")
    assert skill_usage.read_records(deps) == {"version": 1, "skills": {}}


def test_write_records_is_atomic(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_usage.write_records(deps, {"version": 1, "skills": {}})
    leftover = list(tmp_path.glob(".usage.json.tmp.*"))
    assert leftover == [], f"unexpected tmp leftovers: {leftover}"


# ---------------------------------------------------------------------------
# is_agent_created
# ---------------------------------------------------------------------------


def test_is_agent_created_true_for_user_skill(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("my-skill", deps) is True


def test_is_agent_created_false_for_bundled_only(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("doctor", deps) is False


def test_is_agent_created_false_for_nonexistent(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("nope-not-real", deps) is False


# ---------------------------------------------------------------------------
# bump_view / bump_use / bump_patch
# ---------------------------------------------------------------------------


def test_bump_view_creates_record_and_increments(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "my-skill")

    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["view_count"] == 1
    assert record["last_viewed_at"] is not None
    assert record["state"] == "active"
    assert record["pinned"] is False
    assert record["use_count"] == 0


def test_bump_view_repeated_increments_counter(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    for _ in range(3):
        skill_usage.bump_view(deps, "my-skill")
    assert skill_usage.read_records(deps)["skills"]["my-skill"]["view_count"] == 3


def test_bump_view_skips_bundled_skill(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "doctor")
    assert "doctor" not in skill_usage.read_records(deps).get("skills", {})


def test_bump_use_increments_use_count_and_timestamp(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_use(deps, "my-skill")
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["use_count"] == 1
    assert record["view_count"] == 0
    assert record["last_used_at"] is not None
    assert record["last_viewed_at"] is None


def test_bump_patch_increments_patch_count_and_timestamp(tmp_path: Path) -> None:
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


def test_forget_removes_entry(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.record_create(deps, "my-skill")
    assert "my-skill" in skill_usage.read_records(deps)["skills"]
    skill_usage.forget(deps, "my-skill")
    assert "my-skill" not in skill_usage.read_records(deps).get("skills", {})


def test_forget_unknown_skill_is_noop(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_usage.forget(deps, "nonexistent")
    assert skill_usage.read_records(deps) == {"version": 1, "skills": {}}


def test_set_pinned_creates_stub_when_no_record(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_usage.set_pinned(deps, "ghost-skill", True)
    record = skill_usage.read_records(deps)["skills"]["ghost-skill"]
    assert record["pinned"] is True
    assert record["use_count"] == 0
    assert record["created_at"] is not None


def test_set_pinned_toggles_existing_record(tmp_path: Path) -> None:
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
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    config = SETTINGS.model_copy(deep=True)
    config.skills.usage_tracking_enabled = False
    deps = _make_deps(tmp_path, config=config)
    skill_usage.bump_view(deps, "my-skill")
    assert not (tmp_path / ".usage.json").exists()


def test_record_create_short_circuits_when_disabled(tmp_path: Path) -> None:
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
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["created_at"] is not None
    assert record["view_count"] == 0

    await skill_view(ctx, name="my-skill")
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["view_count"] == 1
    assert record["use_count"] == 1
    assert record["last_viewed_at"] is not None
    assert record["last_used_at"] is not None


@pytest.mark.asyncio
async def test_skill_view_on_bundled_does_not_create_sidecar_entry(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_view(ctx, name="doctor")
    assert "doctor" not in skill_usage.read_records(deps).get("skills", {})


@pytest.mark.asyncio
async def test_skill_manage_patch_bumps_patch_count(tmp_path: Path) -> None:
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
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    new_content = "---\ndescription: Edited skill\n---\n\nEdited body.\n"
    await skill_manage(ctx, action="edit", name="my-skill", content=new_content)
    record = skill_usage.read_records(deps)["skills"]["my-skill"]
    assert record["patch_count"] == 1


@pytest.mark.asyncio
async def test_skill_manage_delete_removes_sidecar_entry(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_manage(ctx, action="create", name="my-skill", content=_VALID_CONTENT)
    assert "my-skill" in skill_usage.read_records(deps)["skills"]

    await skill_manage(ctx, action="delete", name="my-skill")
    assert "my-skill" not in skill_usage.read_records(deps).get("skills", {})


def test_sidecar_file_format_matches_spec(tmp_path: Path) -> None:
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
