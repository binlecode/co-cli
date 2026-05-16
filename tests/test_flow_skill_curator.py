"""Behavioral tests for the skill curator runner and the session-review gate.

Phase 2 (the consolidation agent) is exercised only at the wiring level:
without a real model in deps, build_task_agent fails inside run_curator's
Phase 2 try-block and the path falls through to Phase 3. That lets us verify Phase 1
(state transitions) and Phase 3 (report + state write) without depending on
a real LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.config.skills import CURATOR_STALE_AFTER_DAYS
from co_cli.deps import CoDeps, CoSessionState
from co_cli.main import _curator_gate_passes, _maybe_run_curator
from co_cli.skills import usage as skill_usage
from co_cli.skills.curator import read_curator_state, write_curator_state
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_CONTENT = """\
---
description: A skill for curator-runner tests
---

Do the task.
"""


def _make_deps(tmp_path: Path, *, curator_enabled: bool = True) -> CoDeps:
    config = SETTINGS.model_copy(deep=True)
    config.skills.curator_enabled = curator_enabled
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=skills_dir)
    _, tool_index = build_native_toolset(config)
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        tool_index=tool_index,
        session=CoSessionState(),
        skill_index=skill_index,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=skills_dir,
        tool_results_dir=tmp_path / "tool-results",
    )


def _ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# _curator_gate_passes (pure)
# ---------------------------------------------------------------------------


def test_gate_passes_when_never_run() -> None:
    assert _curator_gate_passes({"run_count": 0, "paused": False}, 168, datetime.now(UTC)) is True


def test_gate_blocks_within_interval() -> None:
    now = datetime.now(UTC)
    state = {"last_run_at": _ago(days=1), "paused": False}
    assert _curator_gate_passes(state, 168, now) is False


def test_gate_passes_after_interval() -> None:
    now = datetime.now(UTC)
    state = {"last_run_at": _ago(days=14), "paused": False}
    assert _curator_gate_passes(state, 168, now) is True


def test_gate_blocks_when_paused_even_if_eligible() -> None:
    now = datetime.now(UTC)
    state = {"last_run_at": _ago(days=14), "paused": True}
    assert _curator_gate_passes(state, 168, now) is False


def test_gate_tolerates_unparseable_last_run_at() -> None:
    now = datetime.now(UTC)
    state = {"last_run_at": "not-a-timestamp", "paused": False}
    assert _curator_gate_passes(state, 168, now) is True


# ---------------------------------------------------------------------------
# _maybe_run_curator wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_run_curator_short_circuits_when_disabled(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, curator_enabled=False)
    await _maybe_run_curator(deps)
    state = read_curator_state(deps)
    assert state.get("last_run_at") is None
    assert state.get("run_count", 0) == 0


@pytest.mark.asyncio
async def test_maybe_run_curator_short_circuits_when_no_model(tmp_path: Path) -> None:
    deps = _make_deps(tmp_path, curator_enabled=True)
    assert deps.model is None
    await _maybe_run_curator(deps)
    state = read_curator_state(deps)
    assert state.get("last_run_at") is None


@pytest.mark.asyncio
async def test_maybe_run_curator_blocked_by_recent_run(tmp_path: Path) -> None:
    """A curator state with a fresh last_run_at suppresses the next firing."""
    deps = _make_deps(tmp_path, curator_enabled=True)
    write_curator_state(
        deps,
        {
            "version": 1,
            "last_run_at": _ago(days=1),
            "run_count": 1,
            "paused": False,
        },
    )
    await _maybe_run_curator(deps)
    state = read_curator_state(deps)
    # Still showing prior run_count, not bumped
    assert state["run_count"] == 1


# ---------------------------------------------------------------------------
# run_curator Phase 1 + Phase 3 (Phase 2 errors out without a real model)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_curator_applies_state_transitions(tmp_path: Path) -> None:
    """A stale-eligible skill transitions active → stale during Phase 1."""
    from co_cli.skills.curator import run_curator

    deps = _make_deps(tmp_path, curator_enabled=True)
    (deps.user_skills_dir / "old-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")

    # Seed sidecar so apply_state_transitions sees an idle-active record
    skill_usage.write_records(
        deps,
        {
            "version": 1,
            "skills": {
                "old-skill": {
                    "use_count": 1,
                    "view_count": 1,
                    "patch_count": 0,
                    "created_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 5),
                    "last_used_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 5),
                    "last_viewed_at": _ago(days=CURATOR_STALE_AFTER_DAYS + 5),
                    "last_patched_at": None,
                    "state": "active",
                    "pinned": False,
                }
            },
        },
    )

    await run_curator(deps)

    records = skill_usage.read_records(deps)
    assert records["skills"]["old-skill"]["state"] == "stale"


@pytest.mark.asyncio
async def test_run_curator_writes_state_even_when_phase2_fails(tmp_path: Path) -> None:
    """Phase 3 always writes last_run_at + bumps run_count, even if the agent failed."""
    from co_cli.skills.curator import run_curator

    deps = _make_deps(tmp_path, curator_enabled=True)
    # deps.model is None — Phase 2's build_task_agent will raise; Phase 3 must still run.

    before = read_curator_state(deps)
    assert before.get("run_count", 0) == 0

    await run_curator(deps)

    after = read_curator_state(deps)
    assert after["run_count"] == 1
    assert after.get("last_run_at") is not None
    assert "error" in after.get("last_run_summary", "").lower()
