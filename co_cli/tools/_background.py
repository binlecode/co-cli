"""Background task execution for long-running commands.

Provides TaskStorage (filesystem persistence) and TaskRunner (asyncio process management)
for running shell commands in the background without blocking the interactive chat loop.

Task lifecycle: pending → running → completed | failed | cancelled
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Unsafe chars for filesystem IDs
_UNSAFE_CHARS = re.compile(r'[/\\*?]')


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


def _make_task_id(command: str) -> str:
    """Generate task_YYYYMMDD_HHMMSS_<cmd_name> from command string."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    first_word = command.strip().split()[0] if command.strip() else "task"
    # Use basename only (e.g. /usr/bin/python → python)
    cmd_name = os.path.basename(first_word)
    # Replace unsafe filesystem chars
    cmd_name = _UNSAFE_CHARS.sub("_", cmd_name)[:32]
    return f"task_{ts}_{cmd_name}"


# ---------------------------------------------------------------------------
# TaskStorage — filesystem persistence
# ---------------------------------------------------------------------------

class TaskStorage:
    """Read/write task metadata and output to .co-cli/tasks/<task_id>/."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        return self.base_dir / task_id

    def metadata_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "metadata.json"

    def output_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "output.log"

    def result_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "result.json"

    def create(self, task_id: str, command: str, cwd: str, approval_record: dict | None = None) -> dict:
        """Write initial metadata.json with status=pending."""
        d = self.task_dir(task_id)
        d.mkdir(parents=True, exist_ok=True)
        meta = {
            "task_id": task_id,
            "status": TaskStatus.pending.value,
            "command": command,
            "cwd": cwd,
            "pid": None,
            "exit_code": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "completed_at": None,
            "approval_record": approval_record,
            "span_id": None,
            "is_binary": False,
        }
        self.metadata_path(task_id).write_text(json.dumps(meta, indent=2))
        return meta

    def update(self, task_id: str, **fields) -> dict:
        """Merge fields into existing metadata.json."""
        meta = self.read(task_id)
        meta.update(fields)
        self.metadata_path(task_id).write_text(json.dumps(meta, indent=2))
        return meta

    def read(self, task_id: str) -> dict:
        """Read metadata.json for task_id."""
        return json.loads(self.metadata_path(task_id).read_text())

    def write_result(self, task_id: str, exit_code: int, duration: float | None, summary: str) -> None:
        """Write result.json on task completion."""
        result = {"exit_code": exit_code, "duration": duration, "summary": summary}
        self.result_path(task_id).write_text(json.dumps(result, indent=2))

    def list_tasks(self, status_filter: str | None = None) -> list[dict]:
        """List all tasks, optionally filtered by status string."""
        tasks = []
        for d in sorted(self.base_dir.iterdir()):
            if not d.is_dir():
                continue
            meta_path = d / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text())
                if status_filter is None or meta.get("status") == status_filter:
                    tasks.append(meta)
            except Exception:
                continue
        return tasks

    def delete(self, task_id: str) -> None:
        """Delete task directory and all contents."""
        import shutil
        d = self.task_dir(task_id)
        if d.exists():
            shutil.rmtree(d)

    def cleanup_old(self, retention_days: int) -> int:
        """Delete completed/failed/cancelled tasks older than retention_days. Returns count deleted."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted = 0
        terminal = {TaskStatus.completed.value, TaskStatus.failed.value, TaskStatus.cancelled.value}
        for d in list(self.base_dir.iterdir()):
            if not d.is_dir():
                continue
            meta_path = d / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("status") not in terminal:
                    continue
                # Use completed_at or created_at for age check
                ts_str = meta.get("completed_at") or meta.get("created_at")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str)
                    if ts < cutoff:
                        import shutil
                        shutil.rmtree(d)
                        deleted += 1
            except Exception:
                continue
        return deleted

    def tail_output(self, task_id: str, n: int = 20) -> list[str]:
        """Return last n lines of output.log."""
        p = self.output_path(task_id)
        if not p.exists():
            return []
        try:
            text = p.read_text(errors="replace")
            lines = text.splitlines()
            return lines[-n:]
        except Exception:
            return []

    def output_size(self, task_id: str) -> int:
        """Return current size in bytes of output.log (0 if missing)."""
        p = self.output_path(task_id)
        try:
            return p.stat().st_size
        except FileNotFoundError:
            return 0

    def sniff_binary(self, task_id: str) -> bool:
        """Sniff first 4096 bytes: True if null byte or >30% non-printable chars."""
        p = self.output_path(task_id)
        if not p.exists():
            return False
        try:
            chunk = p.read_bytes()[:4096]
        except Exception:
            return False
        if b"\x00" in chunk:
            return True
        if not chunk:
            return False
        non_printable = sum(1 for b in chunk if b < 0x20 and b not in (0x09, 0x0A, 0x0D))
        return non_printable / len(chunk) > 0.30


# ---------------------------------------------------------------------------
# TaskRunner — asyncio process manager
# ---------------------------------------------------------------------------

class TaskRunner:
    """Singleton async task runner. Created once in main.py before chat_loop()."""

    def __init__(
        self,
        storage: TaskStorage,
        max_concurrent: int = 5,
        inactivity_timeout: int = 0,
        auto_cleanup: bool = True,
        retention_days: int = 7,
    ) -> None:
        self._storage = storage
        self._max_concurrent = max_concurrent
        self._inactivity_timeout = inactivity_timeout
        self._live: dict[str, asyncio.subprocess.Process] = {}
        self._pending: list[tuple[str, str, str]] = []  # (task_id, command, cwd)
        self._monitor_tasks: dict[str, asyncio.Task] = {}

        # Retention cleanup first, then orphan recovery
        if auto_cleanup:
            deleted = storage.cleanup_old(retention_days)
            if deleted:
                logger.info(f"TaskRunner: cleaned up {deleted} old task(s)")

        # Orphan recovery — any status=running is a crash orphan
        for meta in storage.list_tasks(status_filter=TaskStatus.running.value):
            tid = meta["task_id"]
            logger.warning(f"TaskRunner: crash orphan recovered: {tid}")
            storage.update(tid, status=TaskStatus.failed.value, exit_code=-1,
                           completed_at=datetime.now(timezone.utc).isoformat())
            storage.write_result(tid, -1, None, "[crash orphan recovered at startup]")

    async def start_task(self, command: str, cwd: str, approval_record: dict | None = None, span_id: str | None = None) -> str:
        """Create and start (or queue) a background task. Returns task_id."""
        task_id = _make_task_id(command)
        self._storage.create(task_id, command, cwd, approval_record)
        if span_id:
            self._storage.update(task_id, span_id=span_id)

        if len(self._live) < self._max_concurrent:
            await self._spawn(task_id, command, cwd)
        else:
            self._pending.append((task_id, command, cwd))
            logger.info(f"TaskRunner: queued {task_id} (concurrent limit reached)")
        return task_id

    async def _spawn(self, task_id: str, command: str, cwd: str) -> None:
        """Spawn subprocess and start monitor coroutine."""
        output_path = self._storage.output_path(task_id)
        output_fd = open(output_path, "wb")  # noqa: WPS515 — fd kept open for subprocess lifetime
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=output_fd,
                stderr=output_fd,
                cwd=cwd,
                start_new_session=True,
            )
        except Exception as e:
            output_fd.close()
            self._storage.update(task_id, status=TaskStatus.failed.value, exit_code=-1,
                                 completed_at=datetime.now(timezone.utc).isoformat())
            self._storage.write_result(task_id, -1, None, f"[spawn failed: {e}]")
            logger.error(f"TaskRunner: spawn failed for {task_id}: {e}")
            return

        self._live[task_id] = proc
        self._storage.update(
            task_id,
            status=TaskStatus.running.value,
            pid=proc.pid,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info(f"TaskRunner: started {task_id} (pid={proc.pid})")

        monitor = asyncio.create_task(self._monitor(task_id, proc, output_fd))

        if self._inactivity_timeout > 0:
            watcher = asyncio.create_task(self._inactivity_watcher(task_id, proc))
            self._monitor_tasks[task_id] = asyncio.create_task(
                self._run_with_watcher(task_id, monitor, watcher)
            )
        else:
            self._monitor_tasks[task_id] = monitor

    async def _run_with_watcher(
        self, task_id: str, monitor: asyncio.Task, watcher: asyncio.Task
    ) -> None:
        await asyncio.gather(monitor, watcher, return_exceptions=True)

    async def _monitor(self, task_id: str, proc: asyncio.subprocess.Process, output_fd) -> None:
        """Wait for process exit, finalize metadata and result.json."""
        started_at_str = self._storage.read(task_id).get("started_at")
        try:
            await proc.wait()
        finally:
            output_fd.close()

        self._live.pop(task_id, None)
        exit_code = proc.returncode if proc.returncode is not None else -1
        status = TaskStatus.completed.value if exit_code == 0 else TaskStatus.failed.value
        completed_at = datetime.now(timezone.utc).isoformat()
        self._storage.update(task_id, status=status, exit_code=exit_code, completed_at=completed_at)

        # Sniff binary after process exits (full output available)
        is_binary = self._storage.sniff_binary(task_id)
        if is_binary:
            self._storage.update(task_id, is_binary=True)

        # Compute duration
        duration: float | None = None
        if started_at_str:
            try:
                started_at = datetime.fromisoformat(started_at_str)
                duration = (datetime.now(timezone.utc) - started_at).total_seconds()
            except Exception:
                pass

        # Summary = last 10 lines of output
        summary_lines = self._storage.tail_output(task_id, n=10)
        summary = "\n".join(summary_lines) if summary_lines else ""
        self._storage.write_result(task_id, exit_code, duration, summary)
        logger.info(f"TaskRunner: {task_id} {status} (exit={exit_code})")

        # Drain queue
        if self._pending:
            next_id, next_cmd, next_cwd = self._pending.pop(0)
            await self._spawn(next_id, next_cmd, next_cwd)

    async def _inactivity_watcher(self, task_id: str, proc: asyncio.subprocess.Process) -> None:
        """Auto-cancel task if output.log doesn't grow for inactivity_timeout seconds."""
        last_size = -1
        deadline = time.monotonic() + self._inactivity_timeout
        while True:
            await asyncio.sleep(1)
            if task_id not in self._live:
                return
            size = self._storage.output_size(task_id)
            if size != last_size:
                last_size = size
                deadline = time.monotonic() + self._inactivity_timeout
            elif time.monotonic() >= deadline:
                logger.warning(f"TaskRunner: inactivity timeout for {task_id} — cancelling")
                await self._kill(task_id, proc)
                return

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled, False if not running."""
        proc = self._live.get(task_id)
        if proc is None:
            return False
        await self._kill(task_id, proc)
        return True

    async def _kill(self, task_id: str, proc: asyncio.subprocess.Process) -> None:
        """Send SIGTERM → wait 200ms → SIGKILL via process group."""
        from co_cli._shell_env import kill_process_tree
        await kill_process_tree(proc)
        self._live.pop(task_id, None)
        completed_at = datetime.now(timezone.utc).isoformat()
        self._storage.update(task_id, status=TaskStatus.cancelled.value,
                             exit_code=-1, completed_at=completed_at)
        self._storage.write_result(task_id, -1, None, "[cancelled]")

    async def shutdown(self) -> None:
        """Kill all running tasks and wait up to 5s. Called on co-cli exit."""
        if not self._live:
            return
        from co_cli._shell_env import kill_process_tree

        async def _cancel_one(task_id: str, proc: asyncio.subprocess.Process):
            await kill_process_tree(proc)
            completed_at = datetime.now(timezone.utc).isoformat()
            self._storage.update(task_id, status=TaskStatus.cancelled.value,
                                 exit_code=-1, completed_at=completed_at)
            self._storage.write_result(task_id, -1, None, "[shutdown cancelled]")

        live_snapshot = dict(self._live)
        try:
            await asyncio.wait_for(
                asyncio.gather(*[_cancel_one(tid, p) for tid, p in live_snapshot.items()],
                               return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("TaskRunner.shutdown: 5s deadline exceeded — leaving survivors to OS")
        self._live.clear()

        # Cancel asyncio monitor tasks
        for t in self._monitor_tasks.values():
            t.cancel()
        self._monitor_tasks.clear()

    def get_task(self, task_id: str) -> dict | None:
        """Read task metadata; returns None if task_id not found."""
        try:
            return self._storage.read(task_id)
        except FileNotFoundError:
            return None

    def list_tasks(self, status_filter: str | None = None) -> list[dict]:
        return self._storage.list_tasks(status_filter)
