"""Behavioral tests for the session-review iteration counter in _post_turn_hook.

Covers: tick-by-one, tick-by-N, threshold trip, counter reset on spawn,
and single-in-flight gate (no double-spawn while a task is running).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.config.skills import SkillsSettings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import LlmModel
from co_cli.main import _post_turn_hook
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(tmp_path: Path, *, review_nudge_interval: int = 10) -> CoDeps:
    """Minimal CoDeps with review enabled, pointed at tmp_path."""
    skills_settings = SkillsSettings(
        review_enabled=True,
        review_nudge_interval=review_nudge_interval,
    )
    config = SETTINGS_NO_MCP.model_copy(update={"skills": skills_settings})
    deps = CoDeps(shell=ShellBackend(), config=config, session=CoSessionState())
    # Provide a non-None model so the guard in _post_turn_hook passes.
    # LlmModel is a plain dataclass; the model object itself is never called here.
    deps.model = LlmModel(model=object(), settings=None)
    return deps


# ---------------------------------------------------------------------------
# Test 1: text-only turn ticks the counter by 1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_iteration_increments_by_one(tmp_path: Path) -> None:
    """One iteration increments iterations_since_review by exactly 1.

    Verifies that _post_turn_hook accepts turn_iteration_count=1 and adds it
    to iterations_since_review when the threshold has not been reached.
    """
    deps = _make_deps(tmp_path)

    _post_turn_hook(deps, [], 1)

    assert deps.session.iterations_since_review == 1


# ---------------------------------------------------------------------------
# Test 2: multi-iteration turn ticks by N
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_iteration_increments_by_n(tmp_path: Path) -> None:
    """turn_iteration_count=3 increments the counter by 3.

    Verifies that the hook passes the full iteration count through to the
    counter, not just 1 regardless of how many iterations the turn contained.
    """
    deps = _make_deps(tmp_path)

    _post_turn_hook(deps, [], 3)

    assert deps.session.iterations_since_review == 3


# ---------------------------------------------------------------------------
# Test 3: reaching the threshold spawns a background review task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_threshold_trip_spawns_review_task(tmp_path: Path) -> None:
    """Accumulating iterations equal to review_nudge_interval spawns a task.

    Calls the hook enough times to reach the threshold (10 by default) and
    asserts that background_review_task is set afterward.
    """
    deps = _make_deps(tmp_path, review_nudge_interval=10)

    # Accumulate to threshold in two calls: 7 + 3 = 10
    _post_turn_hook(deps, [], 7)
    _post_turn_hook(deps, [], 3)

    task = deps.session.background_review_task
    assert task is not None
    # Clean up to avoid ResourceWarning
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


# ---------------------------------------------------------------------------
# Test 4: counter resets to 0 after spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counter_reset_after_spawn(tmp_path: Path) -> None:
    """iterations_since_review is reset to 0 when a review task is spawned.

    After the threshold is crossed the counter must be zeroed so the next
    window starts fresh.
    """
    deps = _make_deps(tmp_path, review_nudge_interval=10)

    _post_turn_hook(deps, [], 10)

    assert deps.session.iterations_since_review == 0
    # Clean up
    task = deps.session.background_review_task
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# Test 5: in-flight gate — no double-spawn while previous task is running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inflight_gate_blocks_new_spawn(tmp_path: Path) -> None:
    """A second threshold trip while a review task is running does not spawn.

    The single-in-flight contract: when background_review_task is not None and
    not done(), the hook returns early without resetting the counter or
    creating a new task.
    """
    deps = _make_deps(tmp_path, review_nudge_interval=10)

    # Inject a never-completing task to simulate an in-flight review.
    in_flight = asyncio.get_event_loop().create_task(asyncio.sleep(9999))
    deps.session.background_review_task = in_flight
    # Pre-set the counter to just below threshold so one more call trips it.
    deps.session.iterations_since_review = 9

    _post_turn_hook(deps, [], 1)

    # Counter reached threshold (9 + 1 = 10) but in-flight gate must block spawn.
    assert deps.session.background_review_task is in_flight, (
        "background_review_task must not be replaced while the previous task is running"
    )
    assert deps.session.iterations_since_review == 10, (
        "counter must not be reset when the spawn is skipped due to in-flight gate"
    )

    # Cancel the synthetic task to avoid ResourceWarning.
    in_flight.cancel()
    try:
        await in_flight
    except (asyncio.CancelledError, Exception):
        pass
