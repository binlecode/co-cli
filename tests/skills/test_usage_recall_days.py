"""Unit tests for bump_recall: ISO date appended to recall_days, deduplication.

No LLM. Real filesystem writes via real sidecar I/O.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills import usage as skill_usage
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_SKILL_CONTENT = """\
---
description: A skill for bump_recall tests
---

# test-recall-skill

**Invocation:** /test-recall-skill

## Phase 1 — Do it

Do it.
"""


def _make_deps(tmp_path: Path) -> CoDeps:
    user_skills_dir = tmp_path / "skills"
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    # Write skill file so is_agent_created returns True
    (user_skills_dir / "test-recall-skill.md").write_text(_SKILL_CONTENT, encoding="utf-8")
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=user_skills_dir)
    _, tool_index = build_native_toolset(SETTINGS)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index=skill_index,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=user_skills_dir,
        tool_results_dir=tmp_path / "tool-results",
    )


# ---------------------------------------------------------------------------
# Append today's date
# ---------------------------------------------------------------------------


def test_bump_recall_adds_today_iso_date(tmp_path: Path) -> None:
    """bump_recall appends today's ISO date string to recall_days."""
    deps = _make_deps(tmp_path)
    skill_usage.bump_recall(deps, "test-recall-skill")

    record = skill_usage.read_record(deps, "test-recall-skill")
    assert record is not None
    today = date.today().isoformat()
    assert today in record["recall_days"]


def test_bump_recall_starts_from_empty_list(tmp_path: Path) -> None:
    """Before any bump_recall call, no sidecar exists."""
    deps = _make_deps(tmp_path)

    assert skill_usage.read_record(deps, "test-recall-skill") is None

    skill_usage.bump_recall(deps, "test-recall-skill")

    record = skill_usage.read_record(deps, "test-recall-skill")
    assert record is not None
    assert len(record["recall_days"]) == 1


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_bump_recall_deduplicates_same_day(tmp_path: Path) -> None:
    """Calling bump_recall twice on the same day keeps only one entry for today."""
    deps = _make_deps(tmp_path)

    skill_usage.bump_recall(deps, "test-recall-skill")
    skill_usage.bump_recall(deps, "test-recall-skill")

    record = skill_usage.read_record(deps, "test-recall-skill")
    assert record is not None
    today = date.today().isoformat()
    assert record["recall_days"].count(today) == 1


def test_bump_recall_does_not_duplicate_when_already_present(tmp_path: Path) -> None:
    """If today's date is already in recall_days, bump_recall does not add it again."""
    deps = _make_deps(tmp_path)
    today = date.today().isoformat()

    # Seed per-skill sidecar with today already present
    now_iso = skill_usage._utcnow_iso()
    record = skill_usage._new_record(now_iso)
    record["recall_days"] = [today]
    skill_usage.write_record(deps, "test-recall-skill", record)

    skill_usage.bump_recall(deps, "test-recall-skill")

    record = skill_usage.read_record(deps, "test-recall-skill")
    assert record is not None
    assert record["recall_days"].count(today) == 1


# ---------------------------------------------------------------------------
# Usage tracking disabled short-circuits
# ---------------------------------------------------------------------------


def test_bump_recall_no_op_when_usage_tracking_disabled(tmp_path: Path) -> None:
    """bump_recall is a no-op when usage_tracking_enabled is False."""
    config = SETTINGS.model_copy(
        update={"skills": SETTINGS.skills.model_copy(update={"usage_tracking_enabled": False})}
    )
    user_skills_dir = tmp_path / "skills"
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    (user_skills_dir / "test-recall-skill.md").write_text(_SKILL_CONTENT, encoding="utf-8")
    _, tool_index = build_native_toolset(config)
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index={},
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=user_skills_dir,
        tool_results_dir=tmp_path / "tool-results",
    )

    skill_usage.bump_recall(deps, "test-recall-skill")

    # Per-skill sidecar should not have been written
    sidecar = user_skills_dir / "test-recall-skill.usage.json"
    assert not sidecar.exists()


# ---------------------------------------------------------------------------
# Non-agent-created skill is skipped
# ---------------------------------------------------------------------------


def test_bump_recall_skips_skill_not_in_user_skills_dir(tmp_path: Path) -> None:
    """bump_recall skips skills that don't exist under user_skills_dir."""
    deps = _make_deps(tmp_path)

    # Call with a name that has no .md in user_skills_dir
    skill_usage.bump_recall(deps, "no-such-skill")

    assert skill_usage.read_record(deps, "no-such-skill") is None
