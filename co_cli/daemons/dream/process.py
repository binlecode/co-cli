"""Public surface for dream daemon lifecycle management.

Re-exports: start_daemon, stop_daemon, status_daemon.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from pathlib import Path

logger = logging.getLogger(__name__)

from co_cli.config.core import (
    DREAM_PID_FILE,
    DREAM_QUEUE_DIR,
    DREAM_QUEUE_DONE_DIR,
    DREAM_QUEUE_FAILED_DIR,
    LOGS_DIR,
    get_settings,
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
from co_cli.daemons.dream.state import DaemonState
from co_cli.observability.setup import setup_observability

STOP_GRACE_SECONDS = 3.0
_STOP_POLL_INTERVAL_SECONDS = 0.5


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

    force=False (default): SIGTERM, then SIGKILL after STOP_GRACE_SECONDS if still
    alive. The daemon now races bootstrap against shutdown and exits promptly even
    mid-cold-bootstrap, so the grace only has to cover a genuinely wedged process.
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
    for _ in range(int(STOP_GRACE_SECONDS / _STOP_POLL_INTERVAL_SECONDS)):
        time.sleep(_STOP_POLL_INTERVAL_SECONDS)
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
    print(  # noqa: T201
        f"daemon force-killed (did not respond to SIGTERM in {STOP_GRACE_SECONDS:g}s)"
    )


def status_daemon(co_home: Path) -> dict:
    """Return a dict describing the current daemon status (file-based, no socket)."""
    from co_cli.daemons.dream.state import load_pid_state

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

    Order matters: signal handlers install first so a SIGTERM arriving during
    create_deps is observed. The blocking embed in create_deps runs on a worker
    thread (bootstrap/core.py:_sync_indexes_offthread), so the event loop stays
    responsive and bootstrap is raced against shutdown below — a stop requested
    mid-bootstrap cancels bootstrap and exits via os._exit (skipping the executor
    join on the uncancellable embed worker), instead of going deaf to SIGTERM and
    being force-killed after the grace window. Observability is wired before
    write_pid so any crash from this point is captured in
    $CO_HOME/logs/co-dream.jsonl.
    """
    from co_cli.bootstrap.core import create_deps

    pid_file = DREAM_PID_FILE
    queue_dir = DREAM_QUEUE_DIR
    done_dir = DREAM_QUEUE_DONE_DIR
    failed_dir = DREAM_QUEUE_FAILED_DIR

    for directory in (queue_dir, done_dir, failed_dir, pid_file.parent):
        directory.mkdir(parents=True, exist_ok=True)

    setup_observability(
        LOGS_DIR,
        app_log_name="co-dream.jsonl",
        spans_log_name="co-dream-spans.jsonl",
        errors_log_name=None,
        settings=get_settings(),
    )

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown.set)
    loop.add_signal_handler(signal.SIGINT, shutdown.set)

    write_pid(pid_file, os.getpid(), origin, session_id)

    state = DaemonState()

    logger.info("Dream daemon starting (pid=%d, origin=%s)", os.getpid(), origin)
    try:
        bootstrap = asyncio.create_task(create_deps(on_status=logger.info, stack=None))
        shutdown_wait = asyncio.create_task(shutdown.wait())
        await asyncio.wait({bootstrap, shutdown_wait}, return_when=asyncio.FIRST_COMPLETED)
        if not bootstrap.done():
            bootstrap.cancel()
            logger.info("Dream daemon: shutdown during bootstrap — exiting")
            pid_file.unlink(missing_ok=True)
            os._exit(0)
        shutdown_wait.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown_wait
        deps = bootstrap.result()
        await main_loop(deps, queue_dir, done_dir, failed_dir, state, deps.config.dream, shutdown)
    except Exception:
        logger.error("dream daemon crashed", exc_info=True)
        raise
    finally:
        pid_file.unlink(missing_ok=True)
