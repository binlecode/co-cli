"""Session-scoped background task execution — no file I/O."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def _make_task_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def spawn_task(state: BackgroundTaskState, session: "CoSessionState") -> None:
    """Create subprocess; store process on state; launch _monitor coroutine."""
    try:
        proc = await asyncio.create_subprocess_shell(
            state.command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, cwd=state.cwd, start_new_session=True,
        )
    except Exception as e:
        state.status = "failed"
        state.exit_code = -1
        state.completed_at = _now()
        state.output_lines.append(f"[spawn failed: {e}]")
        return
    state.process = proc
    asyncio.create_task(_monitor(state.task_id, session))


async def _monitor(task_id: str, session: "CoSessionState") -> None:
    """Drain stdout/stderr line-by-line into output_lines; update state on EOF."""
    state = session.background_tasks.get(task_id)
    if state is None or state.process is None:
        return
    proc = state.process
    assert proc.stdout is not None
    async for line in proc.stdout:
        state.output_lines.append(line.decode(errors="replace").rstrip("\n"))
    await proc.wait()
    exit_code = proc.returncode if proc.returncode is not None else -1
    state.exit_code = exit_code
    state.status = "completed" if exit_code == 0 else "failed"
    state.completed_at = _now()


async def kill_task(state: BackgroundTaskState) -> None:
    """SIGTERM → 200ms → SIGKILL via process group."""
    from co_cli.tools._shell_env import kill_process_tree
    if state.process is not None:
        await kill_process_tree(state.process)
    state.status = "cancelled"
    state.exit_code = -1
    state.completed_at = _now()
