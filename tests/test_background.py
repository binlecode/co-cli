"""Functional tests for session-scoped background task execution.

Tests exercise real code paths: process spawning, output capture, cancellation,
and the four tool function signatures. All tests spawn real subprocesses (no mocks).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
from tests._timeouts import (
    FILE_DB_TIMEOUT_SECS,
    SUBPROCESS_START_TIMEOUT_SECS,
    SUBPROCESS_TIMEOUT_SECS,
)

from co_cli.deps import CoSessionState
from co_cli.tools.background import BackgroundTaskState, _make_task_id, kill_task, spawn_task


def _fresh_session() -> CoSessionState:
    return CoSessionState(session_id="test-background")


def _make_state(command: str, cwd: str = "/tmp", description: str = "") -> BackgroundTaskState:
    return BackgroundTaskState(
        task_id=_make_task_id(),
        command=command,
        cwd=cwd,
        description=description,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# BackgroundTaskState defaults
# ---------------------------------------------------------------------------


def test_task_state_defaults():
    state = BackgroundTaskState(
        task_id="abc123",
        command="echo hi",
        cwd="/tmp",
        description="test",
        status="running",
        started_at=datetime.now(UTC).isoformat(),
    )
    assert state.output_lines.maxlen == 500
    assert state.process is None
    assert state.completed_at is None
    assert state.exit_code is None
    assert state.cleanup_incomplete is False
    assert state.cleanup_error is None


def test_make_task_id_unique():
    ids = {_make_task_id() for _ in range(20)}
    assert len(ids) == 20
    for tid in ids:
        assert len(tid) == 12


# ---------------------------------------------------------------------------
# spawn_task — process spawning and output capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_and_complete():
    """Spawn a real echo command, wait for completion via monitor."""
    session = _fresh_session()
    state = _make_state("echo hello_bg_test")
    session.background_tasks[state.task_id] = state

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await spawn_task(state, session)

    assert state.process is not None

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        for _ in range(50):
            await asyncio.sleep(0.1)
            if state.status in ("completed", "failed"):
                break

    assert state.status == "completed"
    assert state.exit_code == 0
    assert state.completed_at is not None
    assert any("hello_bg_test" in line for line in state.output_lines)


@pytest.mark.asyncio
async def test_spawn_failed_command():
    """Non-zero exit sets status=failed."""
    session = _fresh_session()
    state = _make_state("false")
    session.background_tasks[state.task_id] = state

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await spawn_task(state, session)

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        for _ in range(30):
            await asyncio.sleep(0.1)
            if state.status == "failed":
                break

    assert state.status == "failed"
    assert state.exit_code != 0


@pytest.mark.asyncio
async def test_spawn_invalid_command():
    """spawn_task with an invalid command sets status=failed immediately."""
    session = _fresh_session()
    # Invalid cwd causes spawn failure
    state = BackgroundTaskState(
        task_id=_make_task_id(),
        command="echo test",
        cwd="/nonexistent_dir_xyz",
        description="",
        status="running",
        started_at=datetime.now(UTC).isoformat(),
    )
    session.background_tasks[state.task_id] = state

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await spawn_task(state, session)

    assert state.status == "failed"
    assert state.exit_code == -1
    assert any("spawn failed" in line for line in state.output_lines)


@pytest.mark.asyncio
async def test_output_captured_in_deque():
    """Output lines accumulate in the bounded deque."""
    session = _fresh_session()
    state = _make_state('printf "line1\\nline2\\nline3\\n"')
    session.background_tasks[state.task_id] = state

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await spawn_task(state, session)

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        for _ in range(50):
            await asyncio.sleep(0.1)
            if state.status == "completed":
                break

    output = list(state.output_lines)
    assert "line1" in output
    assert "line2" in output
    assert "line3" in output


# ---------------------------------------------------------------------------
# kill_task — cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_running_task():
    """kill_task cancels a running sleep — status becomes cancelled."""
    session = _fresh_session()
    state = _make_state("sleep 60")
    session.background_tasks[state.task_id] = state

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await spawn_task(state, session)

    # Wait for process to actually start
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        for _ in range(20):
            await asyncio.sleep(0.1)
            if state.process is not None and state.process.returncode is None:
                break

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await kill_task(state)

    assert state.status == "cancelled"
    assert state.exit_code == -1
    assert state.completed_at is not None
    assert state.cleanup_incomplete is False
    assert state.cleanup_error is None
    assert state.process is None
    assert state._monitor_task is not None
    assert state._monitor_task.done() is True


@pytest.mark.asyncio
async def test_kill_already_completed_task():
    """kill_task on a completed task still sets cancelled/exit_code=-1."""
    session = _fresh_session()
    state = _make_state("echo done")
    session.background_tasks[state.task_id] = state

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await spawn_task(state, session)

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        for _ in range(50):
            await asyncio.sleep(0.1)
            if state.status == "completed":
                break

    # kill_task on already-done process — must not raise
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await kill_task(state)

    assert state.status == "cancelled"
    assert state.cleanup_incomplete is False
    assert state.cleanup_error is None


# ---------------------------------------------------------------------------
# Tool function signatures (contract preservation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_start_background_task_signature():
    """start_background_task returns ToolReturn with task_id and status keys."""
    from tests._settings import test_settings

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend
    from co_cli.tools.task_control import start_background_task

    deps = CoDeps(shell=ShellBackend(), config=test_settings())
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        result = await start_background_task(ctx, "echo tool_test", "test task")

    assert "task_id" in result.metadata
    assert result.metadata["status"] == "running"
    task_id = result.metadata["task_id"]
    assert task_id in deps.session.background_tasks

    # Cleanup
    state = deps.session.background_tasks[task_id]
    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        for _ in range(50):
            await asyncio.sleep(0.1)
            if state.status != "running":
                break
    if state._monitor_task is not None:
        async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
            await state._monitor_task


@pytest.mark.asyncio
async def test_tool_check_task_status_signature():
    """check_task_status returns ToolReturn with expected keys."""
    from tests._settings import test_settings

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend
    from co_cli.tools.task_control import check_task_status, start_background_task

    deps = CoDeps(shell=ShellBackend(), config=test_settings())
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        start_result = await start_background_task(ctx, "echo check_test", "check test")
    task_id = start_result.metadata["task_id"]

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        result = await check_task_status(ctx, task_id, tail_lines=5)

    assert result.metadata["task_id"] == task_id
    assert "status" in result.metadata
    assert "output_lines" in result.metadata
    assert "exit_code" in result.metadata
    assert "is_binary" in result.metadata
    assert result.metadata["is_binary"] is False

    state = deps.session.background_tasks[task_id]
    if state.process is not None:
        async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
            await kill_task(state)
    elif state._monitor_task is not None:
        async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
            await state._monitor_task


@pytest.mark.asyncio
async def test_tool_check_task_status_not_found():
    """check_task_status returns not_found for unknown task_id."""
    from tests._settings import test_settings

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend
    from co_cli.tools.task_control import check_task_status

    deps = CoDeps(shell=ShellBackend(), config=test_settings())
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        result = await check_task_status(ctx, "nonexistent_id_xyz")

    assert result.metadata["status"] == "not_found"


@pytest.mark.asyncio
async def test_tool_cancel_background_task_signature():
    """cancel_background_task returns ToolReturn with cancelled status."""
    from tests._settings import test_settings

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend
    from co_cli.tools.task_control import cancel_background_task, start_background_task

    deps = CoDeps(shell=ShellBackend(), config=test_settings())
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        start_result = await start_background_task(ctx, "sleep 60", "sleep task")
    task_id = start_result.metadata["task_id"]

    # Wait for running
    state = deps.session.background_tasks[task_id]
    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        for _ in range(20):
            await asyncio.sleep(0.1)
            if state.process is not None and state.process.returncode is None:
                break

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        result = await cancel_background_task(ctx, task_id)

    assert result.metadata["task_id"] == task_id
    assert result.metadata["status"] == "cancelled"
    assert state.cleanup_incomplete is False
    assert state.cleanup_error is None
    assert state.process is None
    assert state._monitor_task is not None
    assert state._monitor_task.done() is True


@pytest.mark.asyncio
async def test_tool_list_background_tasks_signature():
    """list_background_tasks returns ToolReturn with tasks and count keys."""
    from tests._settings import test_settings

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend
    from co_cli.tools.task_control import list_background_tasks, start_background_task

    deps = CoDeps(shell=ShellBackend(), config=test_settings())
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        result_empty = await list_background_tasks(ctx)
    assert result_empty.metadata["count"] == 0
    assert result_empty.metadata["tasks"] == []

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await start_background_task(ctx, "sleep 10", "list test")

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        result = await list_background_tasks(ctx)

    assert result.metadata["count"] == 1
    rows = result.metadata["tasks"]
    assert len(rows) == 1
    assert "task_id" in rows[0]
    assert "status" in rows[0]
    assert "command" in rows[0]

    # Cleanup
    from co_cli.tools.background import kill_task

    for s in deps.session.background_tasks.values():
        if s.status == "running":
            await kill_task(s)


# ---------------------------------------------------------------------------
# Session-scoped: no files written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_file_io(tmp_path):
    """Background task lifecycle produces no files in any directory."""
    session = _fresh_session()
    state = _make_state("echo no_files_test")
    session.background_tasks[state.task_id] = state

    async with asyncio.timeout(SUBPROCESS_START_TIMEOUT_SECS):
        await spawn_task(state, session)

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        for _ in range(50):
            await asyncio.sleep(0.1)
            if state.status == "completed":
                break

    if state._monitor_task is not None and not state._monitor_task.done():
        async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
            await state._monitor_task

    # tmp_path must remain empty — no task files written anywhere
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Slash command integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_background_command():
    """Slash /background spawns a task and stores it in session state."""
    from tests._settings import test_settings

    from co_cli.commands._commands import BUILTIN_COMMANDS, CommandContext
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        deps = CoDeps(shell=ShellBackend(), config=test_settings())
        ctx = CommandContext(message_history=[], deps=deps, agent=None)

        await BUILTIN_COMMANDS["background"].handler(ctx, "echo slash_test")

        assert len(deps.session.background_tasks) == 1
        state = next(iter(deps.session.background_tasks.values()))
        assert state.command == "echo slash_test"

        # Cleanup
        from co_cli.tools.background import kill_task

        for s in deps.session.background_tasks.values():
            if s.process is not None:
                await kill_task(s)
            if s._monitor_task is not None:
                async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
                    await s._monitor_task


@pytest.mark.asyncio
async def test_slash_tasks_command():
    """Slash /tasks lists tasks from session state."""
    from datetime import datetime

    from tests._settings import test_settings

    from co_cli.commands._commands import BUILTIN_COMMANDS, CommandContext
    from co_cli.deps import CoDeps
    from co_cli.tools.background import BackgroundTaskState, _make_task_id
    from co_cli.tools.shell_backend import ShellBackend

    deps = CoDeps(shell=ShellBackend(), config=test_settings())
    state = BackgroundTaskState(
        task_id=_make_task_id(),
        command="echo done",
        cwd="/tmp",
        description="",
        status="completed",
        started_at=datetime.now(UTC).isoformat(),
    )
    deps.session.background_tasks[state.task_id] = state

    ctx = CommandContext(message_history=[], deps=deps, agent=None)

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        await BUILTIN_COMMANDS["tasks"].handler(ctx, "")
        await BUILTIN_COMMANDS["tasks"].handler(ctx, "completed")


@pytest.mark.asyncio
async def test_slash_cancel_command():
    """Slash /cancel cancels a running task in session state."""
    from tests._settings import test_settings

    from co_cli.commands._commands import BUILTIN_COMMANDS, CommandContext
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    async with asyncio.timeout(SUBPROCESS_TIMEOUT_SECS):
        deps = CoDeps(shell=ShellBackend(), config=test_settings())
        ctx = CommandContext(message_history=[], deps=deps, agent=None)

        await BUILTIN_COMMANDS["background"].handler(ctx, "sleep 60")
        assert len(deps.session.background_tasks) == 1

        state = next(iter(deps.session.background_tasks.values()))
        task_id = state.task_id

        # Wait for process to start
        for _ in range(20):
            await asyncio.sleep(0.1)
            if state.process is not None and state.process.returncode is None:
                break

        await BUILTIN_COMMANDS["cancel"].handler(ctx, task_id)
        assert state.status == "cancelled"
        assert state.cleanup_incomplete is False
        assert state.cleanup_error is None
        assert state.process is None
        assert state._monitor_task is not None
        assert state._monitor_task.done() is True


# ---------------------------------------------------------------------------
# Tool result metadata: description and started_at fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_task_status_surfaces_description_and_started_at(tmp_path):
    """check_task_status result includes description and started_at from task metadata."""
    from tests._settings import test_settings

    from co_cli.agent import build_agent
    from co_cli.config._core import settings
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend
    from co_cli.tools.task_control import check_task_status

    deps = CoDeps(shell=ShellBackend(), config=test_settings())
    agent = build_agent(config=settings)
    ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())

    task_description = "test-background-task-description"
    state = BackgroundTaskState(
        task_id=_make_task_id(),
        command="echo hello",
        cwd=str(tmp_path),
        description=task_description,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
    )
    deps.session.background_tasks[state.task_id] = state

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        await spawn_task(state, deps.session)
        await asyncio.sleep(0.3)
        result = await check_task_status(ctx, state.task_id)

    assert (result.metadata or {}).get("description") == task_description
    assert (result.metadata or {}).get("started_at") is not None


@pytest.mark.asyncio
async def test_list_background_tasks_surfaces_description(tmp_path):
    """list_background_tasks includes task descriptions in both metadata and display output."""
    from tests._settings import test_settings

    from co_cli.agent import build_agent
    from co_cli.config._core import settings
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend
    from co_cli.tools.task_control import list_background_tasks

    deps = CoDeps(shell=ShellBackend(), config=test_settings())
    agent = build_agent(config=settings)
    ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())

    task_description = "background task list description"
    state = BackgroundTaskState(
        task_id=_make_task_id(),
        command="echo hello",
        cwd=str(tmp_path),
        description=task_description,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
    )
    deps.session.background_tasks[state.task_id] = state

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        await spawn_task(state, deps.session)
        await asyncio.sleep(0.3)
        result = await list_background_tasks(ctx)

    assert result.metadata["count"] == 1
    assert result.metadata["tasks"][0]["description"] == task_description
    assert task_description in result.return_value
