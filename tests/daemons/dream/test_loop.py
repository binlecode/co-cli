"""Behavioral tests for the dream daemon event loop and queue drain logic.

Covers invariants NOT exercised by the integration drain path:
- _drain_queue stops between items when shutdown is set (clean-shutdown bound)
- _drain_queue processes every item in a populated queue (loop iteration)

No LLM — kicks use a nonexistent session_id so process_review returns
immediately (transcript file absent → early return, no exception).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP

from co_cli.config.dream import DreamSettings
from co_cli.daemons.dream import _loop
from co_cli.daemons.dream._queue import write_queue_item
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


def _write_kick(queue_dir: Path, name: str) -> Path:
    path = queue_dir / name
    write_queue_item(
        path,
        {
            "session_id": "no-such-session",
            "domain": "memory",
            "persisted_message_count": 0,
            "attempts": 0,
        },
    )
    return path


@pytest.mark.asyncio
async def test_drain_queue_stops_immediately_when_shutdown_is_set(tmp_path: Path) -> None:
    """_drain_queue returns without processing any items when shutdown is pre-set.

    The between-items shutdown check fires at the top of the drain loop before
    picking up the first item — no processing occurs when shutdown is already set.
    """
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = _write_kick(queue_dir, "2024-01-01T00-00-00.json")

    deps = _make_deps(tmp_path)
    state = _make_state()
    cfg = _make_cfg()
    shutdown = asyncio.Event()
    shutdown.set()

    await _loop._drain_queue(deps, queue_dir, cfg, state, shutdown=shutdown)

    assert item_path.exists(), "queue file must remain when shutdown is pre-set"
    done_path = queue_dir / "done" / "2024-01-01T00-00-00.json"
    assert not done_path.exists(), "done/ must be empty when shutdown is pre-set"


@pytest.mark.asyncio
async def test_drain_queue_drains_multiple_items(tmp_path: Path) -> None:
    """_drain_queue processes all items in the queue when shutdown is not set."""
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    items = [_write_kick(queue_dir, f"2024-01-01T0000-0{i}.json") for i in range(3)]

    deps = _make_deps(tmp_path)
    state = _make_state()
    cfg = _make_cfg()

    await _loop._drain_queue(deps, queue_dir, cfg, state, shutdown=asyncio.Event())

    for item_path in items:
        assert not item_path.exists(), f"{item_path.name} must be removed from queue"
        assert (queue_dir / "done" / item_path.name).exists(), (
            f"{item_path.name} must appear in done/"
        )
