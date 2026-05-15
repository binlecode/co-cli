"""Behavioral tests for REPL-exit cleanup (plan 3.5c TASK-2).

Verifies:
- _drain_and_cleanup cancels a pending background review task and bounded-drains it.
- No inline session-end review fires regardless of iterations_since_review value.
- Cleanup returns within ~2s even when the cancelled task swallows CancelledError.
- _drain_and_cleanup's signature dropped the message_history parameter.

All tests use real CoDeps, real asyncio, no monkeypatching.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP


def _make_deps(tmp_path: Path, *, review_enabled: bool = True, with_model: bool = True):
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
                    "review_nudge_interval": 5,
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


# ---------------------------------------------------------------------------
# Signature contract
# ---------------------------------------------------------------------------


def test_drain_and_cleanup_signature_dropped_message_history() -> None:
    """The (deps, stack) signature must not carry a message_history param."""
    from co_cli.main import _drain_and_cleanup

    sig = inspect.signature(_drain_and_cleanup)
    param_names = list(sig.parameters)
    assert param_names == ["deps", "stack"], f"unexpected params: {param_names}"


def test_drain_and_cleanup_has_single_call_site_passing_two_args() -> None:
    """Grep main.py: exactly one call site, with exactly two arguments."""
    src = Path("co_cli/main.py").read_text()
    # The definition itself + the single caller.
    occurrences = src.count("_drain_and_cleanup(")
    assert occurrences == 2, f"expected 2 occurrences (def + 1 call), got {occurrences}"
    # The call site is `await _drain_and_cleanup(deps, stack)`. No message_history kwarg.
    assert "_drain_and_cleanup(deps, stack)" in src
    assert (
        "message_history=" not in src.split("_drain_and_cleanup(deps, stack)")[1].split(")", 1)[0]
    )


def test_drain_and_cleanup_does_not_call_maybe_run_session_review() -> None:
    """Source grep: _drain_and_cleanup body must not invoke _maybe_run_session_review.

    Plan 3.5c removed the inline session-end review call; turn-boundary firing
    is the only review path.
    """
    src = Path("co_cli/main.py").read_text()
    # Extract the function body — from 'async def _drain_and_cleanup' to the next
    # 'async def' or 'def' at column 0.
    start = src.index("async def _drain_and_cleanup(")
    rest = src[start + 1 :]
    end = rest.index("\nasync def ") if "\nasync def " in rest else rest.index("\ndef ")
    body = src[start : start + 1 + end]
    assert "_maybe_run_session_review" not in body, (
        "inline session-end review must be deleted from _drain_and_cleanup"
    )


# ---------------------------------------------------------------------------
# Cancellation + bounded drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_review_task_cancelled_at_exit(tmp_path: Path) -> None:
    """A still-pending background_review_task is cancelled by _drain_and_cleanup."""
    from co_cli.main import _drain_and_cleanup

    deps = _make_deps(tmp_path)

    async def long_running() -> None:
        await asyncio.sleep(30)

    deps.session.background_review_task = asyncio.create_task(long_running())
    task = deps.session.background_review_task
    assert not task.done()

    async with AsyncExitStack() as stack:
        await _drain_and_cleanup(deps, stack)

    assert task.done()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_drain_bounded_when_task_swallows_cancellation(tmp_path: Path) -> None:
    """Truly stubborn task — swallows every CancelledError in a loop.

    Verifies the 2s wait ceiling: _drain_and_cleanup returns near 2s
    (definitely within 3s, definitely past the bare-cleanup floor of ~0.5s),
    even though the task never terminates.
    """
    from co_cli.main import _drain_and_cleanup

    deps = _make_deps(tmp_path)
    done_flag = asyncio.Event()

    async def stubborn() -> None:
        while not done_flag.is_set():
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                pass

    deps.session.background_review_task = asyncio.create_task(stubborn())
    await asyncio.sleep(0)  # let the task enter its first sleep

    t0 = time.monotonic()
    async with AsyncExitStack() as stack:
        await _drain_and_cleanup(deps, stack)
    elapsed = time.monotonic() - t0

    # The 2s wait ceiling must engage and release. Allow a 1s upper margin for
    # the rest of cleanup (dream cycle disabled by default, sync shell.cleanup).
    assert 1.5 < elapsed < 3.0, f"drain elapsed {elapsed:.2f}s — expected ~2s"

    # Teardown: flip the exit flag and nudge the task once so its current sleep
    # is interrupted, the loop re-checks the flag, and the coroutine returns.
    task = deps.session.background_review_task
    if task is not None and not task.done():
        done_flag.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_drain_when_no_review_task_pending(tmp_path: Path) -> None:
    """background_review_task is None — drain completes without error."""
    from co_cli.main import _drain_and_cleanup

    deps = _make_deps(tmp_path)
    assert deps.session.background_review_task is None

    async with AsyncExitStack() as stack:
        await _drain_and_cleanup(deps, stack)


@pytest.mark.asyncio
async def test_drain_when_review_task_already_done(tmp_path: Path) -> None:
    """A completed background_review_task is left untouched (not cancelled)."""
    from co_cli.main import _drain_and_cleanup

    deps = _make_deps(tmp_path)

    async def finishes_quickly() -> None:
        return None

    task = asyncio.create_task(finishes_quickly())
    await task  # let it complete
    deps.session.background_review_task = task
    assert task.done()
    assert not task.cancelled()

    async with AsyncExitStack() as stack:
        await _drain_and_cleanup(deps, stack)

    # Still done, still not cancelled.
    assert task.done()
    assert not task.cancelled()


# ---------------------------------------------------------------------------
# Exit-time absence of inline review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_does_not_fire_inline_review_regardless_of_counter(
    tmp_path: Path,
) -> None:
    """Even with iterations_since_review well past threshold and review_enabled=True,
    cleanup must not spawn or run an inline review.

    Observable: the SESSION_REVIEWS_DIR remains empty (or whatever it was before).
    """
    import co_cli.config.core as core_mod
    from co_cli.main import _drain_and_cleanup

    deps = _make_deps(tmp_path)
    deps.session.iterations_since_review = 999
    assert deps.session.background_review_task is None

    reviews_dir = core_mod.SESSION_REVIEWS_DIR
    before = {p.name for p in reviews_dir.iterdir()} if reviews_dir.exists() else set()

    async with AsyncExitStack() as stack:
        await _drain_and_cleanup(deps, stack)

    after = {p.name for p in reviews_dir.iterdir()} if reviews_dir.exists() else set()
    assert after == before, "cleanup wrote a session-review report — inline path was hit"
    # No task was spawned during cleanup.
    assert deps.session.background_review_task is None
