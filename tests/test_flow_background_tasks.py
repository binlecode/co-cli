"""Background task output is captured to a per-task log file under logs_dir.

Defends against regression of the file-only output contract: spawn → file
written → tail_log returns last N → kill_task closes handle → cleanup unlinks.
Also covers the spawn-failure path (no file, spawn_error populated).
Covers shell policy enforcement in task_start (DENY commands blocked before spawn).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import (
    BG_TASK_COMPLETION_TIMEOUT_SECS,
    BG_TASK_TEARDOWN_TIMEOUT_SECS,
)

from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.background import (
    BackgroundTaskState,
    kill_task,
    make_task_id,
    spawn_task,
    tail_log,
)
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.shell_env import SAFE_ENV_VARS, build_subprocess_env
from co_cli.tools.tasks.control import task_list, task_start


def _make_task_ctx(tmp_path: Path) -> RunContext[CoDeps]:
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_results_dir=tmp_path / "tool-results",
    )
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="task_start")


def _is_error(result) -> bool:
    return result.metadata is not None and result.metadata.get("error") is True


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
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
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
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
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
    async with asyncio.timeout(BG_TASK_TEARDOWN_TIMEOUT_SECS):
        while not (state.log_path.exists() and state.log_path.stat().st_size > 0):
            await asyncio.sleep(0.05)

    async with asyncio.timeout(BG_TASK_TEARDOWN_TIMEOUT_SECS):
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


# ---------------------------------------------------------------------------
# build_subprocess_env — unit coverage for the central env builder
# ---------------------------------------------------------------------------


def test_build_subprocess_env_merges_extra_keys() -> None:
    env = build_subprocess_env(extra_env={"MY_SKILL_VAR": "hello"})
    assert env["MY_SKILL_VAR"] == "hello"
    assert "PATH" in env


def test_build_subprocess_env_refuses_shadow_key() -> None:
    original_path = build_subprocess_env()["PATH"]
    env = build_subprocess_env(extra_env={"PATH": "/evil", "MY_SKILL_VAR": "ok"})
    assert env["MY_SKILL_VAR"] == "ok"
    assert env["PATH"] == original_path


def test_build_subprocess_env_no_host_leakage() -> None:
    import os

    os.environ["_CO_TEST_SECRET"] = "should-not-leak"
    try:
        env = build_subprocess_env()
        assert "_CO_TEST_SECRET" not in env
        extra_keys = set(env) - SAFE_ENV_VARS - {"PYTHONUNBUFFERED", "PAGER", "GIT_PAGER"}
        assert not extra_keys, f"unexpected keys leaked into subprocess env: {extra_keys}"
    finally:
        del os.environ["_CO_TEST_SECRET"]


# ---------------------------------------------------------------------------
# spawn_task env — background subprocess sees restricted env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_task_uses_restricted_env(tmp_path: Path) -> None:
    import os

    os.environ["_CO_TEST_SECRET"] = "bg-should-not-leak"
    try:
        logs_dir = tmp_path / "logs"
        session = CoSessionState()
        task_id = make_task_id()
        state = _make_state(task_id, "printenv", str(tmp_path))
        session.background_tasks[task_id] = state

        await spawn_task(state, session, logs_dir=logs_dir)
        assert state._monitor_task is not None
        async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
            await state._monitor_task

        output = state.log_path.read_text()
        assert "_CO_TEST_SECRET" not in output
    finally:
        del os.environ["_CO_TEST_SECRET"]


# ---------------------------------------------------------------------------
# task_start shell policy (DENY commands blocked before spawn)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_start_denies_rm_rf(tmp_path: Path) -> None:
    ctx = _make_task_ctx(tmp_path)
    result = await task_start(ctx, command="rm -rf /", description="should be denied")
    assert _is_error(result)
    assert "task_start blocked" in result.return_value


# ---------------------------------------------------------------------------
# task_list status_filter (TaskStatus Literal constraint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_list_status_filter_returns_only_matching_tasks(tmp_path: Path) -> None:
    """task_list(status_filter='running') must exclude completed tasks."""
    session = CoSessionState()
    running_id = make_task_id()
    completed_id = make_task_id()
    session.background_tasks[running_id] = BackgroundTaskState(
        task_id=running_id,
        command="sleep 999",
        cwd=str(tmp_path),
        description="long running",
        status="running",
        started_at="",
    )
    session.background_tasks[completed_id] = BackgroundTaskState(
        task_id=completed_id,
        command="echo done",
        cwd=str(tmp_path),
        description="already done",
        status="completed",
        started_at="",
    )
    deps = CoDeps(shell=ShellBackend(), config=SETTINGS, session=session)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="task_list")

    result = await task_list(ctx, status_filter="running")

    assert result.metadata is not None
    tasks = result.metadata.get("tasks", [])
    assert all(t["status"] == "running" for t in tasks), f"unexpected non-running tasks: {tasks}"
    assert result.metadata.get("count") == 1
    assert running_id in result.return_value
    assert completed_id not in result.return_value
