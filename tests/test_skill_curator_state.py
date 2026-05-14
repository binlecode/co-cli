"""Tests for the curator state machine, archive/restore, and idle gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.agents.core import build_tool_registry
from co_cli.config.skills import (
    CURATOR_ARCHIVE_AFTER_DAYS,
    CURATOR_MIN_IDLE_HOURS,
    CURATOR_STALE_AFTER_DAYS,
    SkillsSettings,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills.curator import (
    _idle_seconds,
    apply_state_transitions,
    archive_skill,
    read_curator_state,
    restore_skill,
    should_run_now,
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


def _make_deps(tmp_path: Path, config=SETTINGS) -> CoDeps:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, config, user_skills_dir=skills_dir)
    tool_registry = build_tool_registry(config)
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_index=dict(tool_registry.tool_index),
        session=CoSessionState(),
        skill_commands=skill_commands,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=skills_dir,
        tool_results_dir=tmp_path / "tool-results",
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _ago(days: float = 0, hours: float = 0, seconds: float = 0) -> str:
    """Return an ISO8601 UTC string for a timestamp in the past."""
    delta = timedelta(days=days, hours=hours, seconds=seconds)
    dt = _now() - delta
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_records(**skill_overrides: dict) -> dict:
    """Build a minimal sidecar-style records dict from per-skill overrides."""
    return {"version": 1, "skills": skill_overrides}


# ---------------------------------------------------------------------------
# apply_state_transitions — state transition matrix
# ---------------------------------------------------------------------------


def test_active_to_stale_when_idle_exceeds_stale_threshold() -> None:
    """active → stale when last_used_at > CURATOR_STALE_AFTER_DAYS ago."""
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
    settings = SkillsSettings()
    now = _now()
    transitions = apply_state_transitions(records, settings, now)
    assert len(transitions) == 1
    t = transitions[0]
    assert t.name == "my-skill"
    assert t.from_state == "active"
    assert t.to_state == "stale"


def test_active_no_transition_when_recently_used() -> None:
    """active stays active when last_used_at is within CURATOR_STALE_AFTER_DAYS."""
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
    settings = SkillsSettings()
    transitions = apply_state_transitions(records, settings, _now())
    assert transitions == []


def test_stale_to_archived_when_idle_exceeds_archive_threshold() -> None:
    """stale → archived when last activity > CURATOR_ARCHIVE_AFTER_DAYS ago."""
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
    settings = SkillsSettings()
    transitions = apply_state_transitions(records, settings, _now())
    assert len(transitions) == 1
    t = transitions[0]
    assert t.name == "old-skill"
    assert t.from_state == "stale"
    assert t.to_state == "archived"


def test_stale_to_active_when_recently_used() -> None:
    """stale → active when last_used_at is within CURATOR_STALE_AFTER_DAYS."""
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
    settings = SkillsSettings()
    transitions = apply_state_transitions(records, settings, _now())
    assert len(transitions) == 1
    t = transitions[0]
    assert t.name == "revived-skill"
    assert t.from_state == "stale"
    assert t.to_state == "active"


def test_pinned_skill_skips_all_transitions() -> None:
    """Pinned skills are exempt from ALL state transitions."""
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
    settings = SkillsSettings()
    transitions = apply_state_transitions(records, settings, _now())
    assert transitions == []


def test_archived_skill_is_skipped() -> None:
    """Already-archived skills are excluded from state transitions."""
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
    settings = SkillsSettings()
    transitions = apply_state_transitions(records, settings, _now())
    assert transitions == []


def test_none_last_used_at_falls_back_to_created_at() -> None:
    """If last_used_at is None, created_at is the idle reference."""
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
    settings = SkillsSettings()
    transitions = apply_state_transitions(records, settings, _now())
    assert len(transitions) == 1
    assert transitions[0].to_state == "stale"


def test_both_timestamps_none_skill_is_skipped() -> None:
    """Skills with both last_used_at and created_at as None are skipped."""
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
    settings = SkillsSettings()
    transitions = apply_state_transitions(records, settings, _now())
    assert transitions == []


def test_apply_state_transitions_does_not_mutate_records() -> None:
    """apply_state_transitions is pure — original records dict is unchanged."""
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
    settings = SkillsSettings()
    apply_state_transitions(records, settings, _now())
    assert records["skills"]["my-skill"]["state"] == original_state


def test_multiple_skills_transitions_computed_independently() -> None:
    """Multiple skills each get their own transition computed independently."""
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
    settings = SkillsSettings()
    transitions = apply_state_transitions(records, settings, _now())
    names_and_targets = {(t.name, t.to_state) for t in transitions}
    assert ("skill-a", "stale") in names_and_targets
    assert ("skill-b", "archived") in names_and_targets
    assert all(t.name != "skill-c" for t in transitions)


# ---------------------------------------------------------------------------
# archive_skill / restore_skill — roundtrip
# ---------------------------------------------------------------------------


def test_archive_and_restore_roundtrip_preserves_content(tmp_path: Path) -> None:
    """archive_skill then restore_skill preserves file content byte-for-byte."""
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
    """archive_skill creates .archive/ directory if it doesn't exist."""
    deps = _make_deps(tmp_path)
    skill_path = deps.user_skills_dir / "my-skill.md"
    skill_path.write_text(_VALID_CONTENT, encoding="utf-8")

    archive_dir = deps.user_skills_dir / ".archive"
    assert not archive_dir.exists()

    archive_skill(deps, "my-skill")
    assert archive_dir.exists()
    assert (archive_dir / "my-skill.md").exists()


def test_archive_skill_idempotent_when_already_archived(tmp_path: Path) -> None:
    """archive_skill is a no-op if source is missing but archive already exists."""
    deps = _make_deps(tmp_path)
    archive_dir = deps.user_skills_dir / ".archive"
    archive_dir.mkdir(parents=True)
    (archive_dir / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")

    archive_skill(deps, "my-skill")

    assert (archive_dir / "my-skill.md").exists()


def test_archive_skill_raises_when_neither_source_nor_archive_exists(tmp_path: Path) -> None:
    """archive_skill raises FileNotFoundError if skill doesn't exist anywhere."""
    deps = _make_deps(tmp_path)
    with pytest.raises(FileNotFoundError, match="not found"):
        archive_skill(deps, "nonexistent-skill")


def test_restore_skill_raises_when_not_in_archive(tmp_path: Path) -> None:
    """restore_skill raises FileNotFoundError if skill is not in archive."""
    deps = _make_deps(tmp_path)
    with pytest.raises(FileNotFoundError, match="not in the archive"):
        restore_skill(deps, "nonexistent-skill")


# ---------------------------------------------------------------------------
# read_curator_state / write_curator_state
# ---------------------------------------------------------------------------


def test_read_curator_state_returns_defaults_when_missing(tmp_path: Path) -> None:
    """read_curator_state returns default dict when state file is absent."""
    deps = _make_deps(tmp_path)
    state = read_curator_state(deps)
    assert state["version"] == 1
    assert state["run_count"] == 0
    assert state["paused"] is False


def test_read_curator_state_returns_defaults_on_corrupt_json(tmp_path: Path) -> None:
    """read_curator_state returns defaults on malformed JSON."""
    deps = _make_deps(tmp_path)
    state_path = deps.user_skills_dir / ".curator_state.json"
    state_path.write_text("{bad json", encoding="utf-8")
    state = read_curator_state(deps)
    assert state["run_count"] == 0


def test_write_then_read_curator_state_roundtrip(tmp_path: Path) -> None:
    """write_curator_state then read_curator_state returns the same data."""
    deps = _make_deps(tmp_path)
    payload = {
        "version": 1,
        "last_run_at": "2026-05-12T14:48:40Z",
        "last_run_summary": "archived 2 skills",
        "run_count": 5,
        "paused": False,
    }
    write_curator_state(deps, payload)
    result = read_curator_state(deps)
    assert result == payload


def test_write_curator_state_is_atomic(tmp_path: Path) -> None:
    """write_curator_state leaves no .tmp files behind."""
    deps = _make_deps(tmp_path)
    write_curator_state(deps, {"version": 1, "run_count": 0, "paused": False})
    leftover = list(deps.user_skills_dir.glob(".curator_state.json.tmp.*"))
    assert leftover == []


# ---------------------------------------------------------------------------
# should_run_now — gate conditions
# ---------------------------------------------------------------------------


def _enabled_settings() -> SkillsSettings:
    return SkillsSettings(curator_enabled=True)


def test_should_run_now_false_when_disabled() -> None:
    """should_run_now returns False when curator_enabled is False."""
    settings = SkillsSettings(curator_enabled=False)
    state = {"paused": False, "run_count": 0}
    assert should_run_now(state, settings, _now(), float("inf")) is False


def test_should_run_now_false_when_paused() -> None:
    """should_run_now returns False when paused is True."""
    settings = _enabled_settings()
    state = {"paused": True, "run_count": 0}
    assert should_run_now(state, settings, _now(), float("inf")) is False


def test_should_run_now_false_when_idle_below_threshold() -> None:
    """should_run_now returns False when idle_seconds < CURATOR_MIN_IDLE_HOURS * 3600."""
    settings = _enabled_settings()
    state = {"paused": False, "run_count": 0}
    idle_seconds = 600
    assert idle_seconds < CURATOR_MIN_IDLE_HOURS * 3600
    assert should_run_now(state, settings, _now(), idle_seconds) is False


def test_should_run_now_true_when_idle_meets_threshold() -> None:
    """should_run_now returns True when idle_seconds >= threshold and no last_run_at."""
    settings = _enabled_settings()
    state = {"paused": False, "run_count": 0}
    idle_seconds = CURATOR_MIN_IDLE_HOURS * 3600
    assert should_run_now(state, settings, _now(), idle_seconds) is True


def test_should_run_now_true_when_interval_elapsed() -> None:
    """should_run_now returns True when interval has elapsed since last run."""
    settings = _enabled_settings()
    now = _now()
    last_run = now - timedelta(hours=settings.curator_interval_hours + 1)
    state = {
        "paused": False,
        "run_count": 1,
        "last_run_at": last_run.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    assert should_run_now(state, settings, now, float("inf")) is True


def test_should_run_now_false_when_interval_not_elapsed() -> None:
    """should_run_now returns False when interval has not yet elapsed."""
    settings = _enabled_settings()
    now = _now()
    last_run = now - timedelta(hours=settings.curator_interval_hours - 1)
    state = {
        "paused": False,
        "run_count": 1,
        "last_run_at": last_run.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    assert should_run_now(state, settings, now, float("inf")) is False


def test_should_run_now_bypass_time_gate_overrides_interval() -> None:
    """bypass_time_gate=True causes should_run_now to ignore last_run_at timing."""
    settings = _enabled_settings()
    now = _now()
    last_run = now - timedelta(hours=1)
    state = {
        "paused": False,
        "run_count": 1,
        "last_run_at": last_run.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    assert should_run_now(state, settings, now, float("inf"), bypass_time_gate=True) is True


def test_should_run_now_bypass_still_gated_by_idle() -> None:
    """bypass_time_gate=True does not bypass the idle check."""
    settings = _enabled_settings()
    state = {"paused": False, "run_count": 0}
    idle_seconds = 60
    assert should_run_now(state, settings, _now(), idle_seconds, bypass_time_gate=True) is False


def test_should_run_now_true_when_last_run_at_is_none() -> None:
    """should_run_now returns True when last_run_at is absent (never run)."""
    settings = _enabled_settings()
    state = {"paused": False, "run_count": 0}
    assert should_run_now(state, settings, _now(), float("inf")) is True


# ---------------------------------------------------------------------------
# _idle_seconds
# ---------------------------------------------------------------------------


def test_idle_seconds_returns_inf_when_last_user_input_at_is_none() -> None:
    """_idle_seconds returns inf when last_user_input_at is None (startup state)."""
    session = CoSessionState()
    assert session.last_user_input_at is None
    result = _idle_seconds(session, _now())
    assert result == float("inf")


def test_idle_seconds_returns_correct_elapsed_time() -> None:
    """_idle_seconds returns approximately the elapsed seconds since last input."""
    session = CoSessionState()
    now = _now()
    session.last_user_input_at = now - timedelta(seconds=300)
    result = _idle_seconds(session, now)
    assert 299 < result < 301
