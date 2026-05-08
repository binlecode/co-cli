"""Background task output is captured to a per-task log file under logs_dir.

Defends against regression of the file-only output contract: spawn → file
written → tail_log returns last N → kill_task closes handle → cleanup unlinks.
Also covers the spawn-failure path (no file, spawn_error populated).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from co_cli.deps import CoSessionState
from co_cli.tools.background import (
    BackgroundTaskState,
    kill_task,
    make_task_id,
    spawn_task,
    tail_log,
)


def _make_state(task_id: str, command: str, cwd: str) -> BackgroundTaskState:
    return BackgroundTaskState(
        task_id=task_id,
        command=command,
        cwd=cwd,
        description="test",
        status="running",
        started_at="",
    )


@pytest.mark.asyncio
async def test_spawn_writes_full_output_to_log_file(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(task_id, "for i in 1 2 3 4 5; do echo line-$i; done", str(tmp_path))
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)
    assert state._monitor_task is not None
    async with asyncio.timeout(10):
        await state._monitor_task

    assert state.status == "completed"
    assert state.exit_code == 0
    assert state.log_path == logs_dir / f"bg-{task_id}.log"
    assert state.log_path.exists()
    contents = state.log_path.read_text().splitlines()
    assert contents == ["line-1", "line-2", "line-3", "line-4", "line-5"]


@pytest.mark.asyncio
async def test_tail_log_returns_last_n_from_oversized_run(tmp_path: Path) -> None:
    """A run larger than the old 2500-line cap: tail_log slices from disk."""
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(task_id, "seq 1 5000", str(tmp_path))
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)
    assert state._monitor_task is not None
    async with asyncio.timeout(15):
        await state._monitor_task

    assert state.status == "completed"
    last_50 = tail_log(state.log_path, 50)
    assert last_50 == [str(i) for i in range(4951, 5001)]
    full = state.log_path.read_text().splitlines()
    assert full[0] == "1"
    assert full[-1] == "5000"
    assert len(full) == 5000


def test_tail_log_empty_or_missing_returns_empty(tmp_path: Path) -> None:
    assert tail_log(None, 10) == []
    assert tail_log(tmp_path / "does-not-exist.log", 10) == []
    empty = tmp_path / "empty.log"
    empty.write_text("")
    assert tail_log(empty, 10) == []


def test_tail_log_n_zero_or_negative_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "bg.log"
    p.write_text("a\nb\nc\n")
    assert tail_log(p, 0) == []
    assert tail_log(p, -1) == []


@pytest.mark.asyncio
async def test_kill_task_closes_log_handle_so_file_can_be_unlinked(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(task_id, "while true; do echo tick; sleep 0.1; done", str(tmp_path))
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)
    assert state.log_path is not None
    async with asyncio.timeout(5):
        while not (state.log_path.exists() and state.log_path.stat().st_size > 0):
            await asyncio.sleep(0.05)

    async with asyncio.timeout(5):
        await kill_task(state)

    assert state.status == "cancelled"
    assert not state.cleanup_incomplete
    state.log_path.unlink()
    assert not state.log_path.exists()


@pytest.mark.asyncio
async def test_spawn_failure_sets_spawn_error_and_no_log_file(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(task_id, "true", str(tmp_path / "definitely-not-a-real-cwd"))
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)

    assert state.status == "failed"
    assert state.exit_code == -1
    assert state.spawn_error is not None
    assert "spawn failed" in state.spawn_error
    assert state.log_path is None
    assert not logs_dir.exists() or not any(logs_dir.iterdir())
