"""Behavioral tests for the turn-boundary post-turn hook (plan 3.5c TASK-1).

Calls _post_turn_hook directly and asserts on observable state — counter on
deps.session.iterations_since_review and the spawned task handle on
deps.session.background_review_task. No monkeypatching. Spawned tasks are
cancelled immediately to avoid LLM calls in the unit-test path.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP


@pytest.fixture(autouse=True)
def _restore_co_home() -> Generator[None, None, None]:
    original = os.environ.get("CO_HOME")
    yield
    if original is None:
        os.environ.pop("CO_HOME", None)
    else:
        os.environ["CO_HOME"] = original


def _make_deps(
    tmp_path: Path,
    *,
    review_enabled: bool,
    interval: int = 5,
    with_model: bool = True,
):
    """Real CoDeps with CO_HOME pointed at tmp_path.

    with_model=True attaches a real LlmModel object (cheap to construct; no
    network calls until invoked). Tests that exercise the spawn path use
    task.cancel() immediately after the hook to prevent any LLM traffic.
    """
    os.environ["CO_HOME"] = str(tmp_path)
    import importlib

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={
            "skills": SETTINGS_NO_MCP.skills.model_copy(
                update={
                    "review_enabled": review_enabled,
                    "review_nudge_interval": interval,
                }
            )
        }
    )
    if with_model:
        from co_cli.llm.factory import build_model

        model = build_model(SETTINGS_NO_MCP.llm)
    else:
        model = None
    deps = CoDeps(shell=ShellBackend(), config=config, model=model)
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
    return deps


async def _cancel_pending_task(deps) -> None:
    task = deps.session.background_review_task
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def test_review_disabled_short_circuits_no_state_mutation(tmp_path: Path) -> None:
    """review_enabled=False — hook returns without bumping counter or spawning task."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=False, interval=5)
    _post_turn_hook(deps, [], turn_iteration_count=10)

    assert deps.session.iterations_since_review == 0
    assert deps.session.background_review_task is None


def test_no_model_short_circuits_no_state_mutation(tmp_path: Path) -> None:
    """deps.model is None — hook returns without bumping counter or spawning task."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, interval=5, with_model=False)
    assert deps.model is None

    _post_turn_hook(deps, [], turn_iteration_count=10)

    assert deps.session.iterations_since_review == 0
    assert deps.session.background_review_task is None


def test_below_threshold_counter_accumulates_no_spawn(tmp_path: Path) -> None:
    """Three sub-threshold turns accumulate; no task spawned."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, interval=10)

    _post_turn_hook(deps, [], turn_iteration_count=2)
    _post_turn_hook(deps, [], turn_iteration_count=3)
    _post_turn_hook(deps, [], turn_iteration_count=4)

    assert deps.session.iterations_since_review == 9
    assert deps.session.background_review_task is None


def test_zero_iteration_turn_does_not_advance(tmp_path: Path) -> None:
    """A turn with no tool-producing ModelResponses (text-only) does not advance the counter."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, interval=5)

    _post_turn_hook(deps, [], turn_iteration_count=0)
    _post_turn_hook(deps, [], turn_iteration_count=0)

    assert deps.session.iterations_since_review == 0
    assert deps.session.background_review_task is None


@pytest.mark.asyncio
async def test_at_threshold_spawns_task_and_resets_counter(tmp_path: Path) -> None:
    """Counter reaching threshold — task spawned, counter reset to 0."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, interval=5)

    _post_turn_hook(deps, [], turn_iteration_count=5)

    task = deps.session.background_review_task
    assert task is not None
    assert isinstance(task, asyncio.Task)
    assert deps.session.iterations_since_review == 0

    await _cancel_pending_task(deps)


@pytest.mark.asyncio
async def test_threshold_overshoot_spawns_once(tmp_path: Path) -> None:
    """A single turn with iter_count > threshold spawns exactly one task."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, interval=5)

    _post_turn_hook(deps, [], turn_iteration_count=12)

    assert deps.session.iterations_since_review == 0
    assert isinstance(deps.session.background_review_task, asyncio.Task)

    await _cancel_pending_task(deps)


@pytest.mark.asyncio
async def test_single_in_flight_skip_does_not_reset_counter(tmp_path: Path) -> None:
    """In-flight task — new trigger is skipped; counter NOT reset so future turns retry."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, interval=5)

    # First trigger spawns the task and resets counter to 0.
    _post_turn_hook(deps, [], turn_iteration_count=5)
    first_task = deps.session.background_review_task
    assert first_task is not None
    assert not first_task.done()
    assert deps.session.iterations_since_review == 0

    # Second trigger while task is still pending. Counter accumulates from 0 to 5+.
    _post_turn_hook(deps, [], turn_iteration_count=6)

    # Single-in-flight: still the same task object, no replacement.
    assert deps.session.background_review_task is first_task
    # Counter NOT reset on skip — it carries the post-skip accumulation.
    assert deps.session.iterations_since_review == 6

    await _cancel_pending_task(deps)


@pytest.mark.asyncio
async def test_after_task_completes_next_trigger_fires(tmp_path: Path) -> None:
    """Once the in-flight task is done, the next eligible trigger spawns a new task."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, interval=5)

    _post_turn_hook(deps, [], turn_iteration_count=5)
    first_task = deps.session.background_review_task
    assert first_task is not None
    await _cancel_pending_task(deps)
    assert first_task.done()

    _post_turn_hook(deps, [], turn_iteration_count=5)
    second_task = deps.session.background_review_task

    assert second_task is not None
    assert second_task is not first_task
    assert deps.session.iterations_since_review == 0

    await _cancel_pending_task(deps)


@pytest.mark.asyncio
async def test_error_or_interrupted_turn_iters_still_advance(tmp_path: Path) -> None:
    """Per BC5, counter advances on turns that returned a TurnResult, even if outcome=error.

    The hook receives an integer tool_iterations from TurnResult; the source of that
    value (success/error/interrupted) is opaque to the hook. Verify the hook treats
    any positive count identically.
    """
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, interval=10)

    # Simulate two error/interrupted turns each contributing 3 tool iters.
    _post_turn_hook(deps, [], turn_iteration_count=3)
    _post_turn_hook(deps, [], turn_iteration_count=3)

    assert deps.session.iterations_since_review == 6
    assert deps.session.background_review_task is None


def test_none_deps_short_circuits(tmp_path: Path) -> None:
    """Defensive guard — hook handles deps=None without raising."""
    from co_cli.main import _post_turn_hook

    _post_turn_hook(None, [], turn_iteration_count=5)  # type: ignore[arg-type]
