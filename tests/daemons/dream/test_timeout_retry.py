"""Behavioral tests for the dream daemon retry and failure escalation logic.

Verifies: attempt counter increments on failure, file moves to failed/ after
max_retry_attempts is exhausted, retry backoff is interruptible by shutdown.

Failure injection: a real session JSONL file is created so process_review
proceeds past the transcript-existence check and calls run_standalone.
run_standalone raises when deps.model is None (no model configured in
SETTINGS_NO_MCP). main_loop catches Exception and applies retry logic.
No LLM or asyncio timing tricks — pure exception propagation.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.config.dream import DreamSettings
from co_cli.daemons.dream import _loop
from co_cli.daemons.dream._queue import read_queue_item, write_queue_item
from co_cli.daemons.dream.state import DaemonState
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend


def _make_state() -> DaemonState:
    return DaemonState(start_time=time.time(), spawn_origin="test", spawn_session_id="sess-test")


def _make_deps(tmp_path: Path) -> CoDeps:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        sessions_dir=sessions_dir,
    )


def _write_session_file(sessions_dir: Path, session_id: str) -> None:
    """Create an empty session JSONL file so process_review proceeds past existence check.

    With deps.model=None, run_standalone raises before any LLM call.
    main_loop catches Exception and applies retry logic.
    """
    session_file = sessions_dir / f"{session_id}.jsonl"
    session_file.write_text("")


def _kick_payload(session_id: str, attempts: int = 0) -> dict:
    return {
        "session_id": session_id,
        "domain": "memory",
        "persisted_message_count": 0,
        "attempts": attempts,
    }


async def _run_until_failed(
    deps,
    queue_dir: Path,
    state: DaemonState,
    cfg: DreamSettings,
    item_name: str,
) -> None:
    """Drive main_loop until the item appears in failed/, then signal shutdown."""
    shutdown = asyncio.Event()
    failed_path = queue_dir / "failed" / item_name

    async def stopper() -> None:
        for _ in range(500):
            if failed_path.exists():
                break
            await asyncio.sleep(0.01)
        shutdown.set()

    await asyncio.gather(
        _loop.main_loop(
            deps, queue_dir, queue_dir / "done", queue_dir / "failed", state, cfg, shutdown
        ),
        stopper(),
    )


@pytest.mark.asyncio
async def test_main_loop_exhausts_retries_and_moves_to_failed(tmp_path: Path) -> None:
    """File moves to failed/ after max_retry_attempts failures.

    With max_retry_attempts=1, the first failure immediately exhausts retries —
    no retry backoff is entered. File is moved to failed/ with attempts=1.
    """
    deps = _make_deps(tmp_path)
    _write_session_file(deps.sessions_dir, "sess-fail-test")

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = queue_dir / "2024-01-01T00-00-00.json"
    write_queue_item(item_path, _kick_payload("sess-fail-test", attempts=0))

    state = _make_state()
    cfg = DreamSettings(
        review_timeout_seconds=30,
        retry_backoff_seconds=1,
        max_retry_attempts=1,
        tick_interval_seconds=1,
    )

    await _run_until_failed(deps, queue_dir, state, cfg, "2024-01-01T00-00-00.json")

    assert not item_path.exists(), "queue file must be removed after exhausting retries"
    failed_path = queue_dir / "failed" / "2024-01-01T00-00-00.json"
    assert failed_path.exists(), "file must be moved to failed/ after max_retry_attempts"
    result = read_queue_item(failed_path)
    assert result.get("attempts") == 1
    assert "last_error" in result


@pytest.mark.asyncio
async def test_main_loop_attempt_counter_written_on_penultimate_failure(
    tmp_path: Path,
) -> None:
    """Attempt counter in the queue file reaches max and triggers move to failed/.

    Seeds the payload with attempts=1 (one prior failure). With max_retry_attempts=2,
    one more failure increments to 2 >= 2 and moves the file to failed/.
    No retry backoff occurs because the exhausted-retry path skips the backoff sleep.
    """
    deps = _make_deps(tmp_path)
    _write_session_file(deps.sessions_dir, "sess-counter-test")

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = queue_dir / "2024-01-01T00-00-00.json"
    write_queue_item(item_path, _kick_payload("sess-counter-test", attempts=1))

    state = _make_state()
    cfg = DreamSettings(
        review_timeout_seconds=30,
        retry_backoff_seconds=1,
        max_retry_attempts=2,
        tick_interval_seconds=1,
    )

    await _run_until_failed(deps, queue_dir, state, cfg, "2024-01-01T00-00-00.json")

    failed_path = queue_dir / "failed" / "2024-01-01T00-00-00.json"
    assert failed_path.exists(), "file must be in failed/ when attempts == max_retry_attempts"
    result = read_queue_item(failed_path)
    assert result.get("attempts") == 2, "attempt counter must be 2 after two failures"


@pytest.mark.asyncio
async def test_main_loop_unknown_domain_lands_in_failed(tmp_path: Path) -> None:
    """A kick with an unknown domain → failed/ (not done/).

    process_review raises ValueError on unknown domain, main_loop catches via
    `except Exception` and applies retry/fail logic. With max_retry_attempts=1
    the first failure moves the file straight to failed/ with last_error set.
    """
    deps = _make_deps(tmp_path)
    _write_session_file(deps.sessions_dir, "sess-bad-domain")

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = queue_dir / "2024-01-01T00-00-00.json"
    write_queue_item(
        item_path,
        {
            "session_id": "sess-bad-domain",
            "domain": "weather",
            "persisted_message_count": 0,
            "attempts": 0,
        },
    )

    state = _make_state()
    cfg = DreamSettings(
        review_timeout_seconds=30,
        retry_backoff_seconds=1,
        max_retry_attempts=1,
        tick_interval_seconds=1,
    )

    await _run_until_failed(deps, queue_dir, state, cfg, "2024-01-01T00-00-00.json")

    done_path = queue_dir / "done" / "2024-01-01T00-00-00.json"
    failed_path = queue_dir / "failed" / "2024-01-01T00-00-00.json"
    assert not done_path.exists(), "unknown-domain kick must NOT be archived as done"
    assert failed_path.exists(), "unknown-domain kick must land in failed/"
    result = read_queue_item(failed_path)
    assert "unknown review domain" in result.get("last_error", "")


@pytest.mark.asyncio
async def test_main_loop_shutdown_interrupts_retry_backoff(tmp_path: Path) -> None:
    """Shutdown during retry backoff wakes the loop immediately.

    Regression guard for the flattened-loop fix: previously the inner _drain_queue
    used bare asyncio.sleep(retry_backoff_seconds), which was not interruptible
    by the shutdown event and could blow past the SIGTERM→SIGKILL budget.

    Setup: one item that fails on first attempt, with max_retry_attempts=5 and
    retry_backoff_seconds=10. After the first failure the loop enters retry
    backoff. We set shutdown ~50 ms into the backoff and assert main_loop
    returns within well under retry_backoff_seconds.
    """
    deps = _make_deps(tmp_path)
    _write_session_file(deps.sessions_dir, "sess-shutdown-test")

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = queue_dir / "2024-01-01T00-00-00.json"
    write_queue_item(item_path, _kick_payload("sess-shutdown-test", attempts=0))

    state = _make_state()
    cfg = DreamSettings(
        review_timeout_seconds=30,
        retry_backoff_seconds=10,
        max_retry_attempts=5,
        tick_interval_seconds=10,
    )
    shutdown = asyncio.Event()

    async def stopper() -> None:
        for _ in range(500):
            payload = read_queue_item(item_path) if item_path.exists() else {}
            if payload.get("attempts", 0) >= 1:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        shutdown.set()

    start = time.monotonic()
    await asyncio.gather(
        _loop.main_loop(
            deps, queue_dir, queue_dir / "done", queue_dir / "failed", state, cfg, shutdown
        ),
        stopper(),
    )
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, (
        f"shutdown during retry backoff must wake the loop quickly; "
        f"elapsed={elapsed:.2f}s, retry_backoff_seconds=10"
    )
    assert item_path.exists(), "queue file must remain pending — retries not exhausted"
    result = read_queue_item(item_path)
    assert result.get("attempts") == 1, "one failed attempt must be recorded"
