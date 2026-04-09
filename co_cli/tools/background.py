"""Session-scoped background task execution — no file I/O."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.deps import CoSessionState


@dataclass
class BackgroundTaskState:
    task_id: str
    command: str
    cwd: str
    description: str
    status: str  # "running" | "completed" | "failed" | "cancelled"
    output_lines: deque[str] = field(default_factory=lambda: deque(maxlen=500))
    process: asyncio.subprocess.Process | None = None
    started_at: str = ""
    completed_at: str | None = None
    exit_code: int | None = None
    cleanup_incomplete: bool = False
    cleanup_error: str | None = None
    # Internal: monitor task handle — awaited by kill_task to drain stdout before returning
    _monitor_task: asyncio.Task | None = field(default=None, repr=False)


def _make_task_id() -> str:
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


async def spawn_task(state: BackgroundTaskState, session: CoSessionState) -> None:
    """Create subprocess; store process on state; launch _monitor coroutine."""
    try:
        proc = await asyncio.create_subprocess_shell(
            state.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=state.cwd,
            start_new_session=True,
        )
    except Exception as e:
        state.status = "failed"
        state.exit_code = -1
        state.completed_at = _now()
        state.output_lines.append(f"[spawn failed: {e}]")
        return
    state.process = proc
    state._monitor_task = asyncio.create_task(_monitor(state.task_id, session))


async def _monitor(task_id: str, session: CoSessionState) -> None:
    """Drain stdout/stderr line-by-line into output_lines; update state on EOF."""
    state = session.background_tasks.get(task_id)
    if state is None or state.process is None:
        return
    proc = state.process
    assert proc.stdout is not None
    async for line in proc.stdout:
        state.output_lines.append(line.decode(errors="replace").rstrip("\n"))
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


class BackgroundCleanupError(RuntimeError):
    """Raised when a killed background task does not finish cleanup in time."""


async def kill_task(state: BackgroundTaskState) -> None:
    """SIGTERM → 200ms → SIGKILL via process group; drain monitor before returning."""
    from co_cli.tools._shell_env import kill_process_tree

    state.cleanup_incomplete = False
    state.cleanup_error = None
    proc: asyncio.subprocess.Process | None = None
    if state.process is not None:
        proc = state.process
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
