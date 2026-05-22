"""Public surface for dream daemon lifecycle management.

Re-exports: start_daemon, stop_daemon, status_daemon.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path

from co_cli.config.core import (
    DREAM_LOCK,
    DREAM_PID_FILE,
    DREAM_QUEUE_DIR,
    DREAM_QUEUE_DONE_DIR,
    DREAM_QUEUE_FAILED_DIR,
    DREAM_SOCK,
)
from co_cli.daemons.dream._ipc import DaemonIPC, send_command
from co_cli.daemons.dream._loop import main_loop
from co_cli.daemons.dream._process import (
    acquire_start_lock,
    double_fork_detach,
    is_pid_live,
    read_pid,
    write_pid,
)
from co_cli.daemons.dream._queue import list_queue_files
from co_cli.daemons.dream._state import DaemonState


def start_daemon(
    co_home: Path,
    *,
    foreground: bool = False,
    origin: str = "manual",
    session_id: str = "",
) -> None:
    """Start the dream daemon if it is not already running."""
    pid_file = DREAM_PID_FILE
    lock_path = DREAM_LOCK

    existing_pid = read_pid(pid_file)
    if existing_pid is not None and is_pid_live(existing_pid):
        print(f"daemon already running (pid {existing_pid})")  # noqa: T201 — CLI status output to user
        return

    try:
        with acquire_start_lock(lock_path):
            if foreground:
                asyncio.run(_run_foreground(co_home, origin, session_id))
            else:
                child_pid = double_fork_detach(
                    [
                        "co",
                        "dream",
                        "start",
                        "--foreground",
                        f"--origin={origin}",
                        f"--session-id={session_id}",
                    ]
                )
                print(f"daemon started (pid {child_pid})")  # noqa: T201 — CLI status output to user
    except BlockingIOError:
        print("daemon start already in progress (lock held)")  # noqa: T201 — CLI status output to user


def stop_daemon(co_home: Path, *, force: bool = False) -> None:
    """Stop the running dream daemon."""
    sock_path = DREAM_SOCK
    pid_file = DREAM_PID_FILE

    if not force:
        reply = asyncio.run(send_command(sock_path, "STOP"))
        if reply is not None:
            return

    # Socket failed or force requested — fall back to SIGTERM
    pid = read_pid(pid_file)
    if pid is not None and is_pid_live(pid):
        os.kill(pid, signal.SIGTERM)
    else:
        print("daemon is not running")  # noqa: T201 — CLI status output to user


def status_daemon(co_home: Path, timeout_ms: int = 2000) -> dict:
    """Return a dict describing the current daemon status."""
    sock_path = DREAM_SOCK

    reply = asyncio.run(send_command(sock_path, "STATUS", timeout_ms=timeout_ms))
    if reply is not None:
        try:
            return json.loads(reply)
        except json.JSONDecodeError:
            pass

    return {
        "running": False,
        "queue_depth": len(list_queue_files(DREAM_QUEUE_DIR)),
        "failed_count": len(list(DREAM_QUEUE_FAILED_DIR.glob("*.json"))),
    }


async def _run_foreground(co_home: Path, origin: str, session_id: str) -> None:
    """Run the daemon in the foreground (called after double-fork or --foreground flag)."""
    from co_cli.daemons.dream._deps import build_codeps_for_daemon

    pid_file = DREAM_PID_FILE
    sock_path = DREAM_SOCK
    queue_dir = DREAM_QUEUE_DIR
    done_dir = DREAM_QUEUE_DONE_DIR
    failed_dir = DREAM_QUEUE_FAILED_DIR

    # Ensure required directories exist
    for directory in (queue_dir, done_dir, failed_dir, sock_path.parent):
        directory.mkdir(parents=True, exist_ok=True)

    write_pid(pid_file, os.getpid(), origin, session_id)

    ipc = DaemonIPC()
    await ipc.start(sock_path)

    state = DaemonState(
        start_time=time.time(),
        spawn_origin=origin,
        spawn_session_id=session_id,
    )

    deps = build_codeps_for_daemon(co_home)

    loop = asyncio.get_running_loop()
    if hasattr(loop, "add_signal_handler"):
        _self = asyncio.current_task()
        loop.add_signal_handler(signal.SIGTERM, _self.cancel)

    try:
        await main_loop(deps, queue_dir, ipc, state, deps.config.dream)
    except asyncio.CancelledError:
        pass
    finally:
        await ipc.close()
        if pid_file.exists():
            pid_file.unlink(missing_ok=True)
        if sock_path.exists():
            sock_path.unlink(missing_ok=True)
