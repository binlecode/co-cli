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

from co_cli.commands.core import dispatch
from co_cli.commands.types import CommandContext
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.proc.env import SAFE_ENV_VARS, build_subprocess_env
from co_cli.tools.background import (
    BackgroundTaskState,
    TaskInputError,
    adopt_running_process,
    close_task_stdin,
    kill_task,
    make_task_id,
    spawn_task,
    tail_log,
    write_to_task,
)
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tasks.control import (
    task_cancel,
    task_close,
    task_list,
    task_start,
    task_status,
    task_write,
)


def _make_task_ctx(tmp_path: Path) -> RunContext[CoDeps]:
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        workspace_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="task_start")


def _make_tool_deps(tmp_path: Path) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        workspace_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )


def _tool_ctx(deps: CoDeps, tool_name: str) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=tool_name)


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
# adopt_running_process — live foreground hand-off (shell_exec auto-yield)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adopt_running_process_captures_prefix_and_live_output(tmp_path: Path) -> None:
    """An externally-spawned live process becomes a tracked task whose log holds
    both the seeded pre-yield prefix and output emitted after adoption, and
    whose final state is published on child exit."""
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    proc = await asyncio.create_subprocess_exec(
        "sh",
        "-c",
        "sleep 0.3; echo later-line",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )

    state = await adopt_running_process(
        proc,
        command="sleep 0.3; echo later-line",
        cwd=str(tmp_path),
        session=session,
        prefix_bytes=b"early-line\n",
        logs_dir=logs_dir,
    )

    assert state.task_id in session.background_tasks
    assert state._monitor_task is not None
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task

    assert state.status == "completed"
    assert state.exit_code == 0
    contents = state.log_path.read_text().splitlines()
    assert "early-line" in contents
    assert "later-line" in contents
    assert contents.index("early-line") < contents.index("later-line")


# ---------------------------------------------------------------------------
# write_to_task / close_task_stdin — interactive stdin drive (Phase 2 helpers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_to_task_drives_stdin_reading_command(tmp_path: Path) -> None:
    """A stdin-reading command advances when fed input via write_to_task."""
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(
        task_id,
        "python3 -u -c \"print('got:', input())\"",
        str(tmp_path),
    )
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)
    await write_to_task(state, "hello-stdin", newline=True)

    assert state._monitor_task is not None
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task

    assert state.status == "completed"
    assert state.exit_code == 0
    contents = state.log_path.read_text().splitlines()
    assert contents == ["got: hello-stdin"]


@pytest.mark.asyncio
async def test_close_task_stdin_lets_eof_reader_complete(tmp_path: Path) -> None:
    """Closing stdin signals EOF so a stdin-draining reader exits cleanly."""
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(
        task_id,
        "python3 -u -c \"import sys; data = sys.stdin.read(); print('read', len(data), 'bytes')\"",
        str(tmp_path),
    )
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)
    await write_to_task(state, "abc", newline=False)
    await close_task_stdin(state)

    assert state._monitor_task is not None
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task

    assert state.status == "completed"
    assert state.exit_code == 0
    assert state.log_path.read_text().splitlines() == ["read 3 bytes"]


@pytest.mark.asyncio
async def test_write_to_completed_task_raises_typed_error(tmp_path: Path) -> None:
    """Writing to a finished task raises TaskInputError, not a crash."""
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(task_id, "echo done", str(tmp_path))
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)
    assert state._monitor_task is not None
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task
    assert state.status == "completed"

    with pytest.raises(TaskInputError):
        await write_to_task(state, "too late", newline=True)


# ---------------------------------------------------------------------------
# task_write / task_close tools (Phase 2 tool surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_write_advances_interactive_prompt(tmp_path: Path) -> None:
    """task_write answers a [y/N] prompt; task_status then shows the post-prompt output."""
    deps = _make_tool_deps(tmp_path)
    cmd = "python3 -u -c \"ans = input('Continue? [y/N] '); print('answer:', ans)\""
    start = await task_start(_tool_ctx(deps, "task_start"), command=cmd, description="prompt")
    assert not _is_error(start)
    task_id = start.metadata["task_id"]
    state = deps.session.background_tasks[task_id]

    write = await task_write(_tool_ctx(deps, "task_write"), task_id=task_id, input="y")
    assert not _is_error(write)

    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task

    status = await task_status(_tool_ctx(deps, "task_status"), task_id=task_id)
    output_lines = status.metadata.get("output_lines", [])
    assert any("answer: y" in line for line in output_lines)


@pytest.mark.asyncio
async def test_task_close_lets_eof_reader_exit(tmp_path: Path) -> None:
    """task_close signals EOF so a stdin-draining reader completes with exit 0."""
    deps = _make_tool_deps(tmp_path)
    cmd = "python3 -u -c \"import sys; d = sys.stdin.read(); print('read', len(d))\""
    start = await task_start(_tool_ctx(deps, "task_start"), command=cmd, description="eof")
    task_id = start.metadata["task_id"]
    state = deps.session.background_tasks[task_id]

    await task_write(_tool_ctx(deps, "task_write"), task_id=task_id, input="abcde", newline=False)
    close = await task_close(_tool_ctx(deps, "task_close"), task_id=task_id)
    assert not _is_error(close)

    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task

    assert state.status == "completed"
    assert state.exit_code == 0
    assert state.log_path.read_text().splitlines() == ["read 5"]


@pytest.mark.asyncio
async def test_task_write_and_close_clean_error_on_not_found(tmp_path: Path) -> None:
    """Both tools return a clean tool_error for an unknown task id, not a crash."""
    deps = _make_tool_deps(tmp_path)
    write = await task_write(_tool_ctx(deps, "task_write"), task_id="nope-xyz", input="x")
    close = await task_close(_tool_ctx(deps, "task_close"), task_id="nope-xyz")
    assert _is_error(write)
    assert _is_error(close)


@pytest.mark.asyncio
async def test_write_after_reader_exits_is_clean_error(tmp_path: Path) -> None:
    """Writing (or closing) after the reader has exited yields a clean tool_error.

    The reader consumes one line and exits; once it is gone the task is no longer
    running, so a further write/close surfaces as tool_error rather than raising.
    """
    deps = _make_tool_deps(tmp_path)
    cmd = "python3 -u -c \"input(); print('done')\""
    start = await task_start(_tool_ctx(deps, "task_start"), command=cmd, description="oneshot")
    task_id = start.metadata["task_id"]
    state = deps.session.background_tasks[task_id]

    first = await task_write(_tool_ctx(deps, "task_write"), task_id=task_id, input="y")
    assert not _is_error(first)
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task
    assert state.status == "completed"

    again = await task_write(_tool_ctx(deps, "task_write"), task_id=task_id, input="more")
    closed = await task_close(_tool_ctx(deps, "task_close"), task_id=task_id)
    assert _is_error(again)
    assert _is_error(closed)


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


@pytest.mark.asyncio
async def test_task_start_work_dir_scopes_to_subdir(tmp_path: Path) -> None:
    """A relative work_dir anchors the task cwd under the workspace dir."""
    sub = tmp_path / "sub"
    sub.mkdir()
    ctx = _make_task_ctx(tmp_path)

    result = await task_start(ctx, command="true", description="scoped", work_dir="sub")

    assert not _is_error(result)
    task_id = result.metadata["task_id"]
    state = ctx.deps.session.background_tasks[task_id]
    assert Path(state.cwd).resolve() == sub.resolve()


@pytest.mark.asyncio
async def test_task_start_work_dir_escape_rejected(tmp_path: Path) -> None:
    """A work_dir resolving outside the workspace is rejected before spawn (BC-1, no escape)."""
    ctx = _make_task_ctx(tmp_path)

    result = await task_start(ctx, command="true", description="escape", work_dir="../..")

    assert _is_error(result)
    assert not ctx.deps.session.background_tasks


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


# ---------------------------------------------------------------------------
# task_status — targeted inspection of a known task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_status_returns_status_and_output_for_completed_task(
    tmp_path: Path,
) -> None:
    """task_status must return status, exit_code, and tail output for a completed task."""
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(task_id, "echo hello-status-test", str(tmp_path))
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task

    deps = CoDeps(shell=ShellBackend(), config=SETTINGS, session=session)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="task_status")

    result = await task_status(ctx, task_id=task_id)

    assert result.metadata is not None
    assert result.metadata.get("status") == "completed"
    assert result.metadata.get("exit_code") == 0
    output_lines = result.metadata.get("output_lines", [])
    assert any("hello-status-test" in line for line in output_lines)


@pytest.mark.asyncio
async def test_task_status_not_found_returns_not_found_status(tmp_path: Path) -> None:
    """task_status with an unknown task_id returns status='not_found', not an error."""
    deps = CoDeps(shell=ShellBackend(), config=SETTINGS, session=CoSessionState())
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="task_status")

    result = await task_status(ctx, task_id="nonexistent-id-xyz")

    assert result.metadata is not None
    assert result.metadata.get("status") == "not_found"
    assert result.metadata.get("error") is not True


# ---------------------------------------------------------------------------
# task_cancel — stop a running task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_cancel_cancels_running_task(tmp_path: Path) -> None:
    """task_cancel must kill a running task and report status='cancelled'."""
    logs_dir = tmp_path / "logs"
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(task_id, "while true; do echo tick; sleep 0.1; done", str(tmp_path))
    session.background_tasks[task_id] = state

    await spawn_task(state, session, logs_dir=logs_dir)
    async with asyncio.timeout(BG_TASK_TEARDOWN_TIMEOUT_SECS):
        while not (
            state.log_path and state.log_path.exists() and state.log_path.stat().st_size > 0
        ):
            await asyncio.sleep(0.05)

    deps = CoDeps(shell=ShellBackend(), config=SETTINGS, session=session)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="task_cancel")

    result = await task_cancel(ctx, task_id=task_id)

    assert result.metadata is not None
    assert result.metadata.get("status") == "cancelled"
    assert state.status == "cancelled"


@pytest.mark.asyncio
async def test_task_cancel_not_found_returns_not_found_status(tmp_path: Path) -> None:
    """task_cancel with unknown task_id returns status='not_found'."""
    deps = CoDeps(shell=ShellBackend(), config=SETTINGS, session=CoSessionState())
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="task_cancel")

    result = await task_cancel(ctx, task_id="nonexistent-xyz")

    assert result.metadata is not None
    assert result.metadata.get("status") == "not_found"


@pytest.mark.asyncio
async def test_task_cancel_already_completed_returns_current_status(tmp_path: Path) -> None:
    """task_cancel on a completed task is a no-op that reports the existing status."""
    session = CoSessionState()
    task_id = make_task_id()
    state = _make_state(task_id, "echo done", str(tmp_path))
    state.status = "completed"
    session.background_tasks[task_id] = state

    deps = CoDeps(shell=ShellBackend(), config=SETTINGS, session=session)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="task_cancel")

    result = await task_cancel(ctx, task_id=task_id)

    assert result.metadata is not None
    assert result.metadata.get("status") == "completed"
    assert state.status == "completed"


# ---------------------------------------------------------------------------
# /tasks command — verb routing (run | cancel | status), folds in /background + /cancel
# ---------------------------------------------------------------------------


def _cmd_ctx(deps: CoDeps) -> CommandContext:
    return CommandContext(
        message_history=[],
        deps=deps,
        frontend=HeadlessFrontend(),
    )


@pytest.mark.asyncio
async def test_tasks_run_launches_background_task(tmp_path: Path) -> None:
    """/tasks run <cmd> spawns a real background task whose output reaches its log."""
    deps = _make_tool_deps(tmp_path)
    await dispatch("/tasks run python3 -u -c \"print('hi-from-run')\"", _cmd_ctx(deps))

    tasks = list(deps.session.background_tasks.values())
    assert len(tasks) == 1
    state = tasks[0]
    async with asyncio.timeout(BG_TASK_COMPLETION_TIMEOUT_SECS):
        await state._monitor_task
    assert "hi-from-run" in state.log_path.read_text()


@pytest.mark.asyncio
async def test_tasks_cancel_terminates_running_task(tmp_path: Path) -> None:
    """/tasks cancel <id> terminates a running task (no longer reports running)."""
    deps = _make_tool_deps(tmp_path)
    task_id = make_task_id()
    state = _make_state(task_id, "sleep 999", str(tmp_path))
    deps.session.background_tasks[task_id] = state
    await spawn_task(state, deps.session, logs_dir=tmp_path / "logs")

    await dispatch(f"/tasks cancel {task_id}", _cmd_ctx(deps))

    async with asyncio.timeout(BG_TASK_TEARDOWN_TIMEOUT_SECS):
        await state._monitor_task
    assert state.status != "running"


@pytest.mark.asyncio
async def test_tasks_run_keyword_not_confused_with_status_filter(tmp_path: Path) -> None:
    """A bare status word ('running') lists; only the exact verb 'run' launches.

    Regression guard: if verb routing matched a prefix, /tasks running would try to
    launch the command 'ning' instead of filtering the list by status.
    """
    deps = _make_tool_deps(tmp_path)
    await dispatch("/tasks running", _cmd_ctx(deps))
    assert not deps.session.background_tasks
