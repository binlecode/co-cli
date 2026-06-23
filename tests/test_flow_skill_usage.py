"""Behavioural tests for per-skill usage tracking sidecars."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills import usage as skill_usage
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.skills import (
    skill_create,
    skill_view,
)

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_CONTENT = """\
---
description: A skill for usage tracking tests
---

Do the test task.
"""


def _make_deps(tmp_path: Path, config=SETTINGS) -> CoDeps:
    skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    _, tool_catalog = build_native_toolset()
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_catalog=tool_catalog,
        session=CoSessionState(),
        skill_catalog=skill_catalog,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="skill_create")


# ---------------------------------------------------------------------------
# read_record / write_record / iter_records
# ---------------------------------------------------------------------------


def test_read_record_returns_none_when_sidecar_missing(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    assert skill_usage.read_record(deps, "anything") is None
    assert list(skill_usage.iter_records(deps)) == []


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    record: dict = {
        "version": 1,
        "use_count": 3,
        "view_count": 0,
        "patch_count": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "last_used_at": None,
        "last_viewed_at": None,
        "last_patched_at": None,
        "state": "active",
        "pinned": True,
        "recall_days": [],
    }
    skill_usage.write_record(deps, "foo", record)

    assert (tmp_path / "foo" / "SKILL.usage.json").exists()
    loaded = skill_usage.read_record(deps, "foo")
    assert loaded == record


def test_read_record_returns_none_on_corrupt_json(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    (tmp_path / "foo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "foo" / "SKILL.usage.json").write_text("{this is not json", encoding="utf-8")
    assert skill_usage.read_record(deps, "foo") is None


def test_iter_records_yields_every_sidecar_with_values(tmp_path: Path) -> None:
    """iter_records globs */SKILL.usage.json and yields each skill's record by name.

    Failure mode: the post-migration glob regressing to flat *.usage.json would
    yield zero records, silently starving curation/decay of every sidecar.
    """
    deps = _make_deps(tmp_path)

    def _rec(use_count: int) -> dict:
        return {
            "version": 1,
            "use_count": use_count,
            "view_count": 0,
            "patch_count": 0,
            "created_at": "2026-01-01T00:00:00Z",
            "last_used_at": None,
            "last_viewed_at": None,
            "last_patched_at": None,
            "state": "active",
            "pinned": False,
            "recall_days": [],
        }

    skill_usage.write_record(deps, "alpha", _rec(5))
    skill_usage.write_record(deps, "beta", _rec(2))

    records = dict(skill_usage.iter_records(deps))
    assert set(records) == {"alpha", "beta"}
    assert records["alpha"]["use_count"] == 5
    assert records["beta"]["use_count"] == 2


# ---------------------------------------------------------------------------
# is_agent_created
# ---------------------------------------------------------------------------


def test_is_agent_created_true_for_user_skill(tmp_path: Path) -> None:
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("my-skill", deps) is True


def test_is_agent_created_false_for_bundled_only(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    assert skill_usage.is_agent_created("doctor", deps) is False


# ---------------------------------------------------------------------------
# bump_view / bump_use / bump_patch
# ---------------------------------------------------------------------------


def test_bump_view_creates_record_and_increments(tmp_path: Path) -> None:
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "my-skill")

    record = skill_usage.read_record(deps, "my-skill")
    assert record is not None
    assert record["view_count"] == 1
    assert record["last_viewed_at"] is not None
    assert record["state"] == "active"
    assert record["pinned"] is False
    assert record["use_count"] == 0


def test_bump_view_skips_bundled_skill(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "doctor")
    assert skill_usage.read_record(deps, "doctor") is None


def test_bump_use_increments_use_count_and_timestamp(tmp_path: Path) -> None:
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_use(deps, "my-skill")
    record = skill_usage.read_record(deps, "my-skill")
    assert record is not None
    assert record["use_count"] == 1
    assert record["view_count"] == 0
    assert record["last_used_at"] is not None
    assert record["last_viewed_at"] is None


def test_bump_patch_increments_patch_count_and_timestamp(tmp_path: Path) -> None:
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_patch(deps, "my-skill")
    record = skill_usage.read_record(deps, "my-skill")
    assert record is not None
    assert record["patch_count"] == 1
    assert record["last_patched_at"] is not None


# ---------------------------------------------------------------------------
# record_create / forget / set_pinned
# ---------------------------------------------------------------------------


def test_record_create_initializes_record(tmp_path: Path) -> None:
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.record_create(deps, "my-skill")
    record = skill_usage.read_record(deps, "my-skill")
    assert record is not None
    assert record["use_count"] == 0
    assert record["view_count"] == 0
    assert record["patch_count"] == 0
    assert record["created_at"] is not None
    assert record["state"] == "active"
    assert record["pinned"] is False


def test_forget_removes_entry(tmp_path: Path) -> None:
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.record_create(deps, "my-skill")
    assert (tmp_path / "my-skill" / "SKILL.usage.json").exists()
    skill_usage.forget(deps, "my-skill")
    assert not (tmp_path / "my-skill" / "SKILL.usage.json").exists()
    assert skill_usage.read_record(deps, "my-skill") is None


def test_forget_unknown_skill_is_noop(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_usage.forget(deps, "nonexistent")
    assert list(skill_usage.iter_records(deps)) == []


def test_set_pinned_creates_stub_when_no_record(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_usage.set_pinned(deps, "ghost-skill", True)
    record = skill_usage.read_record(deps, "ghost-skill")
    assert record is not None
    assert record["pinned"] is True
    assert record["use_count"] == 0
    assert record["created_at"] is not None


def test_set_pinned_toggles_existing_record(tmp_path: Path) -> None:
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)
    skill_usage.bump_view(deps, "my-skill")
    skill_usage.set_pinned(deps, "my-skill", True)
    record = skill_usage.read_record(deps, "my-skill")
    assert record is not None
    assert record["pinned"] is True
    skill_usage.set_pinned(deps, "my-skill", False)
    record = skill_usage.read_record(deps, "my-skill")
    assert record is not None
    assert record["pinned"] is False


# ---------------------------------------------------------------------------
# usage_tracking_enabled=False short-circuit
# ---------------------------------------------------------------------------


def test_bump_view_short_circuits_when_disabled(tmp_path: Path) -> None:
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    config = SETTINGS.model_copy(deep=True)
    config.skills.usage_tracking_enabled = False
    deps = _make_deps(tmp_path, config=config)
    skill_usage.bump_view(deps, "my-skill")
    assert not (tmp_path / "my-skill" / "SKILL.usage.json").exists()


# ---------------------------------------------------------------------------
# best-effort error swallowing
# ---------------------------------------------------------------------------


def test_bump_view_swallows_write_failures(tmp_path: Path) -> None:
    """A real OS write failure during bump_view is swallowed; no tmp file leftovers."""
    (tmp_path / "my-skill").mkdir(parents=True, exist_ok=True)
    (tmp_path / "my-skill" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    sidecar_path = tmp_path / "my-skill" / "SKILL.usage.json"
    sidecar_path.mkdir()
    deps = _make_deps(tmp_path)

    skill_usage.bump_view(deps, "my-skill")

    assert sidecar_path.is_dir()
    leftovers = list(tmp_path.glob("my-skill/SKILL.usage.json.tmp.*"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# integration via skill_create / skill_view tools (fan-out wiring smoke)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_create_then_view_produces_sidecar_entry(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    ctx = _make_ctx(deps)
    await skill_create(ctx, name="my-skill", content=_VALID_CONTENT)
    record = skill_usage.read_record(deps, "my-skill")
    assert record is not None
    assert record["created_at"] is not None
    assert record["view_count"] == 0

    await skill_view(ctx, name="my-skill")
    record = skill_usage.read_record(deps, "my-skill")
    assert record is not None
    assert record["view_count"] == 1
    assert record["use_count"] == 1
    assert record["last_viewed_at"] is not None
    assert record["last_used_at"] is not None


# ---------------------------------------------------------------------------
# Per-skill isolation
# ---------------------------------------------------------------------------


def test_bump_one_skill_does_not_touch_another(tmp_path: Path) -> None:
    """bump_use on skill A does not modify skill B's sidecar."""
    (tmp_path / "alpha").mkdir(parents=True, exist_ok=True)
    (tmp_path / "alpha" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    (tmp_path / "beta").mkdir(parents=True, exist_ok=True)
    (tmp_path / "beta" / "SKILL.md").write_text(_VALID_CONTENT, encoding="utf-8")
    deps = _make_deps(tmp_path)

    skill_usage.bump_use(deps, "alpha")
    skill_usage.bump_use(deps, "beta")
    beta_path = tmp_path / "beta" / "SKILL.usage.json"
    beta_mtime_before = beta_path.stat().st_mtime_ns

    for _ in range(3):
        skill_usage.bump_use(deps, "alpha")

    assert beta_path.stat().st_mtime_ns == beta_mtime_before
    alpha = skill_usage.read_record(deps, "alpha")
    beta = skill_usage.read_record(deps, "beta")
    assert alpha is not None
    assert alpha["use_count"] == 4
    assert beta is not None
    assert beta["use_count"] == 1
