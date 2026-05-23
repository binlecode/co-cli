"""Public surface for dream daemon lifecycle management.

Re-exports: start_daemon, stop_daemon, status_daemon.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from pathlib import Path

logger = logging.getLogger(__name__)

from co_cli.config.core import (
    DREAM_LOG_DIR,
    DREAM_PID_FILE,
    DREAM_QUEUE_DIR,
    DREAM_QUEUE_DONE_DIR,
    DREAM_QUEUE_FAILED_DIR,
)
from co_cli.daemons.dream._loop import main_loop
from co_cli.daemons.dream._process import (
    acquire_start_lock,
    is_pid_live,
    read_pid,
    spawn_detached,
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

    existing_pid = read_pid(pid_file)
    if existing_pid is not None and is_pid_live(existing_pid):
        print(f"daemon already running (pid {existing_pid})")  # noqa: T201
        raise SystemExit(1)

    if existing_pid is not None and not is_pid_live(existing_pid):
        print("stale PID file found — overwriting")  # noqa: T201
        pid_file.unlink(missing_ok=True)

    try:
        with acquire_start_lock(DREAM_PID_FILE.with_suffix(".lock")):
            if foreground:
                asyncio.run(_run_foreground(co_home, origin, session_id))
            else:
                child_pid = spawn_detached(
                    [
                        "co",
                        "dream",
                        "start",
                        "--foreground",
                        f"--origin={origin}",
                        f"--session-id={session_id}",
                    ]
                )
                print(f"daemon started (pid {child_pid})")  # noqa: T201
    except BlockingIOError:
        print("daemon start already in progress (lock held)")  # noqa: T201


def stop_daemon(co_home: Path, *, force: bool = False) -> None:
    """Stop the running dream daemon.

    force=False (default): SIGTERM, then SIGKILL after 10s if still alive.
    force=True: SIGKILL immediately, no grace period.

    Always unlinks the PID file after the process is confirmed dead — SIGKILL
    bypasses the daemon's own finally-cleanup, so this is the only path that
    guarantees the file is gone.
    """
    pid_file = DREAM_PID_FILE
    pid = read_pid(pid_file)
    if pid is None or not is_pid_live(pid):
        if pid_file.exists():
            pid_file.unlink(missing_ok=True)
        print("daemon is not running")  # noqa: T201
        return

    if force:
        os.kill(pid, signal.SIGKILL)
        for _ in range(20):
            time.sleep(0.1)
            if not is_pid_live(pid):
                break
        pid_file.unlink(missing_ok=True)
        print(f"daemon force-killed (pid {pid})")  # noqa: T201
        return

    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.5)
        if not is_pid_live(pid):
            pid_file.unlink(missing_ok=True)
            print("daemon stopped")  # noqa: T201
            return
    os.kill(pid, signal.SIGKILL)
    for _ in range(20):
        time.sleep(0.1)
        if not is_pid_live(pid):
            break
    pid_file.unlink(missing_ok=True)
    print("daemon force-killed (did not respond to SIGTERM in 10s)")  # noqa: T201


def status_daemon(co_home: Path) -> dict:
    """Return a dict describing the current daemon status (file-based, no socket)."""
    from co_cli.daemons.dream._state import load_pid_state

    pid_file = DREAM_PID_FILE
    if not pid_file.exists():
        return {
            "running": False,
            "queue_depth": len(list_queue_files(DREAM_QUEUE_DIR)),
            "failed_count": len(list(DREAM_QUEUE_FAILED_DIR.glob("*.json"))),
        }
    pid = read_pid(pid_file)
    if pid is None or not is_pid_live(pid):
        return {
            "running": False,
            "queue_depth": len(list_queue_files(DREAM_QUEUE_DIR)),
            "failed_count": len(list(DREAM_QUEUE_FAILED_DIR.glob("*.json"))),
        }
    pid_data = load_pid_state(pid_file)
    started_at = pid_data.get("started_at")
    uptime_seconds: float | None = None
    if started_at:
        from datetime import UTC, datetime

        try:
            uptime_seconds = (
                datetime.now(UTC) - datetime.fromisoformat(started_at)
            ).total_seconds()
        except ValueError:
            pass
    return {
        "running": True,
        "pid": pid,
        "uptime_seconds": uptime_seconds,
        "queue_depth": len(list_queue_files(DREAM_QUEUE_DIR)),
        "failed_count": len(list(DREAM_QUEUE_FAILED_DIR.glob("*.json"))),
        "spawn_origin": pid_data.get("origin"),
        "spawn_session_id": pid_data.get("session_id"),
    }


async def _run_foreground(co_home: Path, origin: str, session_id: str) -> None:
    """Run the daemon in the foreground (called after detach or --foreground flag).

    Order matters: signal handlers install first so SIGTERM during the
    (potentially several-second) create_deps call still triggers clean shutdown.
    File logging is wired before write_pid so any crash from this point is
    captured in $CO_HOME/logs/dream/<ts>.log.
    """
    from co_cli.bootstrap.core import create_deps

    pid_file = DREAM_PID_FILE
    queue_dir = DREAM_QUEUE_DIR
    done_dir = DREAM_QUEUE_DONE_DIR
    failed_dir = DREAM_QUEUE_FAILED_DIR

    for directory in (queue_dir, done_dir, failed_dir, pid_file.parent, DREAM_LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    _install_daemon_log_handler()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown.set)
    loop.add_signal_handler(signal.SIGINT, shutdown.set)

    write_pid(pid_file, os.getpid(), origin, session_id)

    state = DaemonState(
        start_time=time.time(),
        spawn_origin=origin,
        spawn_session_id=session_id,
    )

    logger.info("Dream daemon starting (pid=%d, origin=%s)", os.getpid(), origin)
    try:
        deps = await create_deps(on_status=logger.info, stack=None)
        await main_loop(deps, queue_dir, state, deps.config.dream, shutdown)
    finally:
        pid_file.unlink(missing_ok=True)


def _install_daemon_log_handler() -> None:
    """Attach a FileHandler to the root logger writing to DREAM_LOG_DIR/<ts>.log.

    Idempotent: subsequent calls within the same process (e.g., test reuse) are
    no-ops based on a sentinel attribute on the handler.
    """
    from datetime import UTC, datetime

    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_dream_daemon_handler", False):
            return

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    log_path = DREAM_LOG_DIR / f"{ts}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._dream_daemon_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)
