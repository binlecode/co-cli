"""Tests for the curator state machine, archive/restore, state persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.config.skills import (
    CURATOR_ARCHIVE_AFTER_DAYS,
    CURATOR_STALE_AFTER_DAYS,
    SkillsSettings,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills.curator import (
    apply_state_transitions,
    archive_skill,
    read_curator_state,
    restore_skill,
    write_curator_state,
)
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_CONTENT = """\
---
description: A skill for curator tests
---

Do the curator test task.
"""


def _make_deps(tmp_path: Path) -> CoDeps:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=skills_dir)
    _, tool_index = build_native_toolset(SETTINGS)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index=skill_index,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=skills_dir,
        tool_results_dir=tmp_path / "tool-results",
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _ago(days: float = 0, hours: float = 0, seconds: float = 0) -> str:
    delta = timedelta(days=days, hours=hours, seconds=seconds)
    dt = _now() - delta
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_records(**skill_overrides: dict) -> dict:
    return {"version": 1, "skills": skill_overrides}


# ---------------------------------------------------------------------------
# apply_state_transitions — state transition matrix
# ---------------------------------------------------------------------------


def test_active_to_stale_when_idle_exceeds_stale_threshold() -> None:
    records = _make_records(
        **{
            "my-skill": {
                "state": "active",
                "pinned": False,
                "last_used_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 1),
                "created_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 2),
            }
        }
    )
    transitions = apply_state_transitions(records, SkillsSettings(), _now())
    assert len(transitions) == 1
    t = transitions[0]
    assert (t.name, t.from_state, t.to_state) == ("my-skill", "active", "stale")


def test_active_no_transition_when_recently_used() -> None:
    records = _make_records(
        **{
            "my-skill": {
                "state": "active",
                "pinned": False,
                "last_used_at": _ago(days=CURATOR_STALE_AFTER_DAYS - 5),
                "created_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 10),
            }
        }
    )
    assert apply_state_transitions(records, SkillsSettings(), _now()) == []


def test_stale_to_archived_when_idle_exceeds_archive_threshold() -> None:
    records = _make_records(
        **{
            "old-skill": {
                "state": "stale",
                "pinned": False,
                "last_used_at": _ago(days=CURATOR_ARCHIVE_AFTER_DAYS + 1),
                "created_at": _ago(days=CURATOR_ARCHIVE_AFTER_DAYS + 5),
            }
        }
    )
    transitions = apply_state_transitions(records, SkillsSettings(), _now())
    assert len(transitions) == 1
    assert (transitions[0].from_state, transitions[0].to_state) == ("stale", "archived")


def test_stale_to_active_when_recently_used() -> None:
    records = _make_records(
        **{
            "revived-skill": {
                "state": "stale",
                "pinned": False,
                "last_used_at": _ago(days=CURATOR_STALE_AFTER_DAYS - 1),
                "created_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 30),
            }
        }
    )
    transitions = apply_state_transitions(records, SkillsSettings(), _now())
    assert len(transitions) == 1
    assert (transitions[0].from_state, transitions[0].to_state) == ("stale", "active")


def test_pinned_skill_skips_all_transitions() -> None:
    records = _make_records(
        **{
            "pinned-skill": {
                "state": "active",
                "pinned": True,
                "last_used_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 10),
                "created_at": _ago(days=CURATOR_ARCHIVE_AFTER_DAYS + 5),
            }
        }
    )
    assert apply_state_transitions(records, SkillsSettings(), _now()) == []


def test_archived_skill_is_skipped() -> None:
    records = _make_records(
        **{
            "dead-skill": {
                "state": "archived",
                "pinned": False,
                "last_used_at": _ago(days=CURATOR_ARCHIVE_AFTER_DAYS + 10),
                "created_at": _ago(days=CURATOR_ARCHIVE_AFTER_DAYS + 20),
            }
        }
    )
    assert apply_state_transitions(records, SkillsSettings(), _now()) == []


def test_none_last_used_at_falls_back_to_created_at() -> None:
    records = _make_records(
        **{
            "new-skill": {
                "state": "active",
                "pinned": False,
                "last_used_at": None,
                "created_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 2),
            }
        }
    )
    transitions = apply_state_transitions(records, SkillsSettings(), _now())
    assert len(transitions) == 1
    assert transitions[0].to_state == "stale"


def test_both_timestamps_none_skill_is_skipped() -> None:
    records = _make_records(
        **{
            "ghost-skill": {
                "state": "active",
                "pinned": False,
                "last_used_at": None,
                "created_at": None,
            }
        }
    )
    assert apply_state_transitions(records, SkillsSettings(), _now()) == []


def test_apply_state_transitions_does_not_mutate_records() -> None:
    records = _make_records(
        **{
            "my-skill": {
                "state": "active",
                "pinned": False,
                "last_used_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 5),
                "created_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 6),
            }
        }
    )
    original_state = records["skills"]["my-skill"]["state"]
    apply_state_transitions(records, SkillsSettings(), _now())
    assert records["skills"]["my-skill"]["state"] == original_state


def test_multiple_skills_transitions_computed_independently() -> None:
    records = _make_records(
        **{
            "skill-a": {
                "state": "active",
                "pinned": False,
                "last_used_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 5),
                "created_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 6),
            },
            "skill-b": {
                "state": "stale",
                "pinned": False,
                "last_used_at": _ago(days=CURATOR_ARCHIVE_AFTER_DAYS + 5),
                "created_at": _ago(days=CURATOR_ARCHIVE_AFTER_DAYS + 6),
            },
            "skill-c": {
                "state": "active",
                "pinned": True,
                "last_used_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 5),
                "created_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 6),
            },
        }
    )
    transitions = apply_state_transitions(records, SkillsSettings(), _now())
    names_and_targets = {(t.name, t.to_state) for t in transitions}
    assert ("skill-a", "stale") in names_and_targets
    assert ("skill-b", "archived") in names_and_targets
    assert all(t.name != "skill-c" for t in transitions)


# ---------------------------------------------------------------------------
# archive_skill / restore_skill — roundtrip
# ---------------------------------------------------------------------------


def test_archive_and_restore_roundtrip_preserves_content(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_path = deps.user_skills_dir / "my-skill.md"
    skill_path.write_bytes(_VALID_CONTENT.encode("utf-8"))

    original_bytes = skill_path.read_bytes()

    archive_skill(deps, "my-skill")
    assert not skill_path.exists()
    archive_path = deps.user_skills_dir / ".archive" / "my-skill.md"
    assert archive_path.exists()
    assert archive_path.read_bytes() == original_bytes

    restore_skill(deps, "my-skill")
    assert skill_path.exists()
    assert not archive_path.exists()
    assert skill_path.read_bytes() == original_bytes


def test_archive_skill_creates_archive_dir(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    skill_path = deps.user_skills_dir / "my-skill.md"
    skill_path.write_text(_VALID_CONTENT, encoding="utf-8")

    archive_dir = deps.user_skills_dir / ".archive"
    assert not archive_dir.exists()

    archive_skill(deps, "my-skill")
    assert archive_dir.exists()
    assert (archive_dir / "my-skill.md").exists()


def test_archive_skill_idempotent_when_already_archived(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    archive_dir = deps.user_skills_dir / ".archive"
    archive_dir.mkdir(parents=True)
    (archive_dir / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")

    archive_skill(deps, "my-skill")
    assert (archive_dir / "my-skill.md").exists()


def test_archive_skill_raises_when_neither_source_nor_archive_exists(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    with pytest.raises(FileNotFoundError, match="not found"):
        archive_skill(deps, "nonexistent-skill")


def test_restore_skill_raises_when_not_in_archive(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    with pytest.raises(FileNotFoundError, match="not in the archive"):
        restore_skill(deps, "nonexistent-skill")


# ---------------------------------------------------------------------------
# read_curator_state / write_curator_state
# ---------------------------------------------------------------------------


def test_read_curator_state_returns_defaults_when_missing(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    state = read_curator_state(deps)
    assert state["version"] == 1
    assert state["run_count"] == 0
    assert state["paused"] is False


def test_read_curator_state_returns_defaults_on_corrupt_json(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    state_path = deps.user_skills_dir / ".curator_state.json"
    state_path.write_text("{bad json", encoding="utf-8")
    state = read_curator_state(deps)
    assert state["run_count"] == 0


def test_write_then_read_curator_state_roundtrip(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    payload = {
        "version": 1,
        "last_run_at": "2026-05-12T14:48:40Z",
        "last_run_summary": "archived 2 skills",
        "run_count": 5,
        "paused": False,
    }
    write_curator_state(deps, payload)
    assert read_curator_state(deps) == payload


def test_write_curator_state_is_atomic(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path)
    write_curator_state(deps, {"version": 1, "run_count": 0, "paused": False})
    leftover = list(deps.user_skills_dir.glob(".curator_state.json.tmp.*"))
    assert leftover == []
