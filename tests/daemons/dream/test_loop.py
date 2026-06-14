"""Behavioral tests for the dream daemon main_loop.

Covers invariants NOT exercised by the integration drain path:
- main_loop exits immediately when shutdown is pre-set (no items touched)
- main_loop drains every item in a populated queue then exits on shutdown

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
        "poll_interval_seconds": 1,
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
async def test_main_loop_exits_immediately_when_shutdown_preset(tmp_path: Path) -> None:
    """main_loop returns without processing any items when shutdown is pre-set."""
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    item_path = _write_kick(queue_dir, "2024-01-01T00-00-00.json")

    deps = _make_deps(tmp_path)
    state = _make_state()
    cfg = _make_cfg()
    shutdown = asyncio.Event()
    shutdown.set()

    await _loop.main_loop(
        deps, queue_dir, queue_dir / "done", queue_dir / "failed", state, cfg, shutdown
    )

    assert item_path.exists(), "queue file must remain when shutdown is pre-set"
    done_path = queue_dir / "done" / "2024-01-01T00-00-00.json"
    assert not done_path.exists(), "done/ must be empty when shutdown is pre-set"


@pytest.mark.asyncio
async def test_main_loop_drains_multiple_items_then_exits(tmp_path: Path) -> None:
    """main_loop processes all items then sleeps in idle-poll; shutdown wakes it."""
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    items = [_write_kick(queue_dir, f"2024-01-01T0000-0{i}.json") for i in range(3)]

    deps = _make_deps(tmp_path)
    state = _make_state()
    cfg = _make_cfg()
    shutdown = asyncio.Event()

    async def stopper() -> None:
        done_dir = queue_dir / "done"
        for _ in range(200):
            if done_dir.exists() and len(list(done_dir.glob("*.json"))) >= 3:
                break
            await asyncio.sleep(0.01)
        shutdown.set()

    await asyncio.gather(
        _loop.main_loop(
            deps, queue_dir, queue_dir / "done", queue_dir / "failed", state, cfg, shutdown
        ),
        stopper(),
    )

    for item_path in items:
        assert not item_path.exists(), f"{item_path.name} must be removed from queue"
        assert (queue_dir / "done" / item_path.name).exists(), (
            f"{item_path.name} must appear in done/"
        )


@pytest.mark.asyncio
async def test_corrupt_kick_lands_in_injected_failed_dir(tmp_path: Path) -> None:
    """A corrupt KICK moves to the injected failed_dir, not queue_dir.parent/'failed'."""
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    injected_failed = tmp_path / "canonical_failed"
    drifted_failed = queue_dir.parent / "failed"

    bad = queue_dir / "2024-01-01T00-00-00.json"
    bad.write_text("{not valid json")

    deps = _make_deps(tmp_path)
    state = _make_state()
    cfg = _make_cfg()
    shutdown = asyncio.Event()

    async def stopper() -> None:
        moved = injected_failed / bad.name
        for _ in range(200):
            if moved.exists():
                break
            await asyncio.sleep(0.01)
        shutdown.set()

    await asyncio.gather(
        _loop.main_loop(
            deps, queue_dir, queue_dir / "done", injected_failed, state, cfg, shutdown
        ),
        stopper(),
    )

    assert (injected_failed / bad.name).exists(), "corrupt KICK must land in injected failed_dir"
    assert not bad.exists(), "corrupt KICK must be removed from the queue"
    assert not (drifted_failed / bad.name).exists(), "must NOT use queue_dir.parent/'failed'"


@pytest.mark.asyncio
async def test_successful_kick_lands_in_injected_done_dir(tmp_path: Path) -> None:
    """A successfully processed KICK moves to the injected done_dir."""
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    injected_done = tmp_path / "canonical_done"
    item_path = _write_kick(queue_dir, "2024-01-01T00-00-01.json")

    deps = _make_deps(tmp_path)
    state = _make_state()
    cfg = _make_cfg()
    shutdown = asyncio.Event()

    async def stopper() -> None:
        moved = injected_done / item_path.name
        for _ in range(200):
            if moved.exists():
                break
            await asyncio.sleep(0.01)
        shutdown.set()

    await asyncio.gather(
        _loop.main_loop(
            deps, queue_dir, injected_done, queue_dir / "failed", state, cfg, shutdown
        ),
        stopper(),
    )

    assert (injected_done / item_path.name).exists(), "KICK must land in injected done_dir"
    assert not item_path.exists(), "processed KICK must be removed from the queue"
