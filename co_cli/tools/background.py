"""Session-scoped background task execution.

Each task's stdout+stderr is streamed to a per-task log file under LOGS_DIR
(`bg-{task_id}.log`). The file is the single source of truth for task output;
no in-memory buffer. Reads (`task_status`, `/tasks`) tail the file. Files are
unlinked at session shutdown by `_drain_and_cleanup`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from co_cli.config.core import LOGS_DIR
from co_cli.tools.shell_env import build_subprocess_env

if TYPE_CHECKING:
    from co_cli.deps import CoSessionState

TaskStatus = Literal["running", "completed", "failed", "cancelled"]


@dataclass
class BackgroundTaskState:
    task_id: str
    command: str
    cwd: str
    description: str
    status: TaskStatus
    log_path: Path | None = None
    spawn_error: str | None = None
    process: asyncio.subprocess.Process | None = None
    started_at: str = ""
    completed_at: str | None = None
    exit_code: int | None = None
    skill_env: dict[str, str] = field(default_factory=dict)
    cleanup_incomplete: bool = False
    cleanup_error: str | None = None
    # Internal: monitor task handle — awaited by kill_task to drain stdout before returning
    _monitor_task: asyncio.Task | None = field(default=None, repr=False)


class TaskInputError(RuntimeError):
    """Raised when writing to a task's stdin fails — task not running or pipe closed."""


def make_task_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _close_process_transport(proc: asyncio.subprocess.Process) -> None:
    """Close the asyncio subprocess transport after stdout has been fully drained."""
    transport = getattr(proc, "_transport", None)
    if transport is not None and not transport.is_closing():
        # asyncio.Process.wait() only waits for exit; it does not close the
        # transport object that owns the read pipe callbacks.
        transport.close()


async def spawn_task(
    state: BackgroundTaskState,
    session: CoSessionState,
    logs_dir: Path = LOGS_DIR,
) -> None:
    """Create subprocess; store process on state; launch _monitor coroutine.

    `logs_dir` is created BEFORE spawning the subprocess so that an mkdir
    failure cannot leave a running process with no `log_path` and no
    monitor coroutine to drain its stdout.
    """
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_shell(
            state.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=state.cwd,
            env=build_subprocess_env(extra_env=state.skill_env or None),
            start_new_session=True,
        )
    except Exception as e:
        state.status = "failed"
        state.exit_code = -1
        state.completed_at = _now()
        state.spawn_error = f"spawn failed: {e}"
        return
    state.log_path = logs_dir / f"bg-{state.task_id}.log"
    state.process = proc
    state._monitor_task = asyncio.create_task(_monitor(state.task_id, session))


async def _monitor(task_id: str, session: CoSessionState) -> None:
    """Drain stdout/stderr line-by-line into the task's log file; update state on EOF."""
    state = session.background_tasks.get(task_id)
    if state is None or state.process is None or state.log_path is None:
        return
    proc = state.process
    assert proc.stdout is not None
    with open(state.log_path, "w", buffering=1) as f:
        async for line in proc.stdout:
            f.write(line.decode(errors="replace").rstrip("\n") + "\n")
        # Wait for the child exit after EOF so returncode is final before state is
        # published as completed/failed.
        await proc.wait()
    # Process.wait() does not close the subprocess transport. Close it
    # explicitly after stdout reaches EOF so asyncio does not defer cleanup to
    # BaseSubprocessTransport.__del__ on a closed event loop.
    _close_process_transport(proc)
    await asyncio.sleep(0)
    exit_code = proc.returncode if proc.returncode is not None else -1
    state.exit_code = exit_code
    state.status = "completed" if exit_code == 0 else "failed"
    state.completed_at = _now()
    state.process = None


async def write_to_task(state: BackgroundTaskState, data: str, newline: bool) -> None:
    """Write UTF-8 text to a running task's stdin and drain.

    Both the buffered write and the drain are wrapped in one try because a
    BrokenPipe surfaces on drain (the write only buffers), not on the write
    call. Raises TaskInputError when the task is not running or its stdin has
    been closed by the child (EOF / exit).
    """
    proc = state.process
    if state.status != "running" or proc is None or proc.stdin is None:
        raise TaskInputError(f"task {state.task_id} is not running — cannot write to stdin")
    payload = (data + "\n") if newline else data
    try:
        proc.stdin.write(payload.encode())
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError) as e:
        raise TaskInputError(
            f"task {state.task_id} is no longer accepting input (stdin closed)"
        ) from e


async def close_task_stdin(state: BackgroundTaskState) -> None:
    """Close a task's stdin to signal EOF. No-op if already closed or no process."""
    proc = state.process
    if proc is not None and proc.stdin is not None and not proc.stdin.is_closing():
        proc.stdin.close()


def tail_log(path: Path | None, n: int) -> list[str]:
    """Return the last n lines of a log file, or [] if path is None or missing.

    Reads from the end with a 64 KB seek window — sufficient for the typical
    n ≤ 100 case without loading the whole file. Falls back to a full read
    when the file is smaller than the window.
    """
    if path is None or not path.exists() or n <= 0:
        return []
    window = 64 * 1024
    try:
        size = path.stat().st_size
        if size == 0:
            return []
        with open(path, "rb") as f:
            if size <= window:
                data = f.read()
            else:
                f.seek(size - window, os.SEEK_SET)
                data = f.read()
        text = data.decode(errors="replace")
        lines = text.splitlines()
        return lines[-n:]
    except OSError:
        return []


class BackgroundCleanupError(RuntimeError):
    """Raised when a killed background task does not finish cleanup in time."""


async def kill_task(state: BackgroundTaskState) -> None:
    """SIGTERM → 200ms → SIGKILL via process group; drain monitor before returning."""
    from co_cli.tools.shell_env import kill_process_tree

    state.cleanup_incomplete = False
    state.cleanup_error = None
    proc: asyncio.subprocess.Process | None = None
    if state.process is not None:
        proc = state.process
        # Defensive: close stdin (EOF) before the kill so a child blocked on a
        # read of its never-closed input pipe can unwind. Cosmetic — the
        # process-group teardown below is what guarantees death, so a BrokenPipe
        # here must never abort the kill path.
        if proc.stdin is not None and not proc.stdin.is_closing():
            try:
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass
        # kill_process_tree handles the process group so shell children do not
        # outlive the tracked background task.
        await kill_process_tree(proc)
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=1.0)
        except TimeoutError as e:
            state.cleanup_incomplete = True
            state.cleanup_error = "process did not exit after cancellation within 1.0s"
            state.completed_at = _now()
            raise BackgroundCleanupError(state.cleanup_error) from e
    # Await monitor task so stdout pipe drains and the subprocess transport closes
    # cleanly before the caller returns. Without this, the transport is left open
    # and triggers PytestUnraisableExceptionWarning when GC runs __del__ on a
    # closed event loop.
    if state._monitor_task is not None and not state._monitor_task.done():
        try:
            # The monitor owns stdout draining. Shield it so our timeout does
            # not cancel the cleanup work and leave transport teardown half-done.
            await asyncio.wait_for(asyncio.shield(state._monitor_task), timeout=1.0)
        except asyncio.CancelledError:
            raise
        except TimeoutError as e:
            state.cleanup_incomplete = True
            state.cleanup_error = "monitor did not drain subprocess output within 1.0s"
            state.completed_at = _now()
            raise BackgroundCleanupError(state.cleanup_error) from e
    if proc is not None:
        _close_process_transport(proc)
    await asyncio.sleep(0)
    state.status = "cancelled"
    state.exit_code = -1
    state.completed_at = _now()
    state.process = None
