"""Behavioral tests for the dream daemon event loop and queue drain logic.

Verifies: list_queue_files chronological sort, drain moves file to done on success.
No LLM — success path uses a nonexistent session_id so process_review returns
immediately (transcript file absent → early return, no exception).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.config.dream import DreamSettings
from co_cli.daemons.dream import _loop
from co_cli.daemons.dream._queue import list_queue_files, write_queue_item
from co_cli.daemons.dream._state import DaemonState
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend


def _make_state() -> DaemonState:
    return DaemonState(start_time=time.time(), spawn_origin="test", spawn_session_id="sess-test")


def _make_cfg(**overrides) -> DreamSettings:
    defaults = {
        "review_timeout_seconds": 30,
        "retry_backoff_seconds": 1,
        "max_retry_attempts": 3,
    }
    defaults.update(overrides)
    return DreamSettings(**defaults)


def _make_deps(tmp_path: Path) -> CoDeps:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        sessions_dir=sessions_dir,
    )


def test_list_queue_files_chronological_order(tmp_path: Path) -> None:
    """list_queue_files returns files sorted oldest-first by filename."""
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    (queue_dir / "2024-01-03T12-00-00.json").write_text("{}")
    (queue_dir / "2024-01-01T08-00-00.json").write_text("{}")
    (queue_dir / "2024-01-02T10-00-00.json").write_text("{}")

    result = list_queue_files(queue_dir)

    names = [p.name for p in result]
    assert names == [
        "2024-01-01T08-00-00.json",
        "2024-01-02T10-00-00.json",
        "2024-01-03T12-00-00.json",
    ]


@pytest.mark.asyncio
async def test_drain_queue_moves_file_to_done_on_success(tmp_path: Path) -> None:
    """_drain_queue moves the queue file to done/ when processing succeeds.

    Uses a session_id with no matching session file. process_review checks
    transcript_path.exists() → False → logs warning and returns without error.
    _drain_queue then moves the kick file to done/.
    """
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = queue_dir / "2024-01-01T00-00-00.json"
    write_queue_item(
        item_path,
        {
            "session_id": "no-such-session",
            "domain": "memory",
            "persisted_message_count": 0,
            "attempts": 0,
        },
    )

    deps = _make_deps(tmp_path)
    state = _make_state()
    cfg = _make_cfg()

    await _loop._drain_queue(deps, queue_dir, cfg, state)

    assert not item_path.exists(), "queue file must be removed after successful processing"
    done_path = queue_dir / "done" / "2024-01-01T00-00-00.json"
    assert done_path.exists(), "queue file must be moved to done/"
