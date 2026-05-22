"""Behavioral tests for the two-counter model in _post_turn_hook.

Covers: memory-turn counter tick, skill-iter counter tick, threshold trip
(counter reset after KICK fires), and repeated threshold trips producing
independent KICKs per crossing (queue provides back-pressure, no in-flight gate).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.config.skills import SkillsSettings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import LlmModel
from co_cli.main import _post_turn_hook
from co_cli.tools.shell_backend import ShellBackend


@pytest.fixture(autouse=True)
def _restore_co_home(tmp_path: Path):
    original = os.environ.get("CO_HOME")
    os.environ["CO_HOME"] = str(tmp_path)
    import importlib

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)
    yield
    if original is None:
        os.environ.pop("CO_HOME", None)
    else:
        os.environ["CO_HOME"] = original


def _make_deps(
    *,
    memory_nudge_interval: int = 10,
    skill_nudge_interval: int = 10,
) -> CoDeps:
    """Minimal CoDeps with review enabled."""
    skills_settings = SkillsSettings(
        review_enabled=True,
        review_memory_nudge_interval=memory_nudge_interval,
        review_skill_nudge_interval=skill_nudge_interval,
    )
    config = SETTINGS_NO_MCP.model_copy(update={"skills": skills_settings})
    deps = CoDeps(shell=ShellBackend(), config=config, session=CoSessionState())
    # Provide a non-None model so the guard in _post_turn_hook passes.
    # LlmModel is a plain dataclass; the model object itself is never called here.
    deps.model = LlmModel(model=object(), settings=None)
    return deps


# ---------------------------------------------------------------------------
# Test 1: memory counter bumps +1 per turn regardless of iteration count
# ---------------------------------------------------------------------------


def test_single_turn_increments_memory_counter_by_one() -> None:
    """One call increments turns_since_memory_review by exactly 1."""
    deps = _make_deps()

    _post_turn_hook(deps, [], 1)

    assert deps.session.turns_since_memory_review == 1


# ---------------------------------------------------------------------------
# Test 2: skill counter bumps +N per call
# ---------------------------------------------------------------------------


def test_multi_iteration_increments_skill_counter_by_n() -> None:
    """turn_iteration_count=3 increments iters_since_skill_review by 3."""
    deps = _make_deps()

    _post_turn_hook(deps, [], 3)

    assert deps.session.iters_since_skill_review == 3


# ---------------------------------------------------------------------------
# Test 3: memory threshold trip resets memory counter
# ---------------------------------------------------------------------------


def test_memory_threshold_trip_resets_counter() -> None:
    """Accumulating turns equal to review_memory_nudge_interval resets the counter."""
    deps = _make_deps(memory_nudge_interval=5, skill_nudge_interval=100)

    # Five turns of 1 = reaches threshold
    for _ in range(5):
        _post_turn_hook(deps, [], 1)

    # Counter reset to 0 (KICK fired)
    assert deps.session.turns_since_memory_review == 0


# ---------------------------------------------------------------------------
# Test 4: counter resets to 0 after KICK fires
# ---------------------------------------------------------------------------


def test_memory_counter_reset_after_kick() -> None:
    """turns_since_memory_review is reset to 0 when memory KICK fires."""
    deps = _make_deps(memory_nudge_interval=5, skill_nudge_interval=100)

    for _ in range(5):
        _post_turn_hook(deps, [], 1)

    assert deps.session.turns_since_memory_review == 0


def test_skill_counter_reset_after_kick() -> None:
    """iters_since_skill_review is reset to 0 when skill KICK fires."""
    deps = _make_deps(memory_nudge_interval=100, skill_nudge_interval=10)

    _post_turn_hook(deps, [], 10)

    assert deps.session.iters_since_skill_review == 0


# ---------------------------------------------------------------------------
# Test 5: no in-flight gate — each threshold trip fires independently
# ---------------------------------------------------------------------------


def test_repeated_threshold_trips_each_reset_counter() -> None:
    """Each time the threshold is crossed, the counter resets independently.

    Unlike the old single-in-flight model, the queue provides back-pressure —
    there is no guard blocking a second KICK if the threshold is hit again.
    """
    deps = _make_deps(memory_nudge_interval=1, skill_nudge_interval=100)

    _post_turn_hook(deps, [], 1)
    assert deps.session.turns_since_memory_review == 0

    _post_turn_hook(deps, [], 1)
    assert deps.session.turns_since_memory_review == 0
