"""Behavioral tests for the dream daemon retry and failure escalation logic.

Verifies: attempt counter increments on failure, file moves to failed/ after
max_retry_attempts is exhausted.

Failure injection: a real session JSONL file is created so process_review
proceeds past the transcript-existence check and calls run_standalone.
run_standalone raises when deps.model is None (no model configured in
SETTINGS_NO_MCP). _drain_queue catches Exception and applies retry logic.
No LLM or asyncio timing tricks — pure exception propagation.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.config.dream import DreamSettings
from co_cli.daemons.dream import _loop
from co_cli.daemons.dream._queue import read_queue_item, write_queue_item
from co_cli.daemons.dream._state import DaemonState
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
    _drain_queue catches Exception and applies retry logic.
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


@pytest.mark.asyncio
async def test_drain_queue_exhausts_retries_and_moves_to_failed(tmp_path: Path) -> None:
    """File moves to failed/ after max_retry_attempts failures.

    With max_retry_attempts=1, the first failure immediately exhausts retries —
    no asyncio.sleep is called. File is moved to failed/ with attempts=1.
    """
    deps = _make_deps(tmp_path)
    _write_session_file(deps.sessions_dir, "sess-fail-test")

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = queue_dir / "2024-01-01T00-00-00.json"
    write_queue_item(item_path, _kick_payload("sess-fail-test", attempts=0))

    state = _make_state()
    cfg = DreamSettings(review_timeout_seconds=30, retry_backoff_seconds=1, max_retry_attempts=1)

    await _loop._drain_queue(deps, queue_dir, cfg, state)

    assert not item_path.exists(), "queue file must be removed after exhausting retries"
    failed_path = queue_dir / "failed" / "2024-01-01T00-00-00.json"
    assert failed_path.exists(), "file must be moved to failed/ after max_retry_attempts"
    result = read_queue_item(failed_path)
    assert result.get("attempts") == 1
    assert "last_error" in result


@pytest.mark.asyncio
async def test_drain_queue_attempt_counter_written_on_penultimate_failure(
    tmp_path: Path,
) -> None:
    """Attempt counter in the queue file reaches max and triggers move to failed/.

    Seeds the payload with attempts=1 (one prior failure). With max_retry_attempts=2,
    one more failure increments to 2 >= 2 and moves the file to failed/.
    No asyncio.sleep occurs because the exhausted-retry path skips the backoff sleep.
    """
    deps = _make_deps(tmp_path)
    _write_session_file(deps.sessions_dir, "sess-counter-test")

    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = queue_dir / "2024-01-01T00-00-00.json"
    write_queue_item(item_path, _kick_payload("sess-counter-test", attempts=1))

    state = _make_state()
    cfg = DreamSettings(review_timeout_seconds=30, retry_backoff_seconds=1, max_retry_attempts=2)

    await _loop._drain_queue(deps, queue_dir, cfg, state)

    failed_path = queue_dir / "failed" / "2024-01-01T00-00-00.json"
    assert failed_path.exists(), "file must be in failed/ when attempts == max_retry_attempts"
    result = read_queue_item(failed_path)
    assert result.get("attempts") == 2, "attempt counter must be 2 after two failures"
