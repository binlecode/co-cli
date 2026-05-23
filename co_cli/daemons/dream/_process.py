"""Dream daemon process management helpers.

POSIX-only: fcntl.flock, double-fork detach, POSIX signals (SIGTERM/SIGKILL).
co-cli is darwin/linux-first; no Windows path is provided.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def is_pid_live(pid: int) -> bool:
    """Return True if the process with the given PID is running.

    PermissionError (EPERM) means the process exists but is owned by a user we
    cannot signal — still alive, so we return True. Only ProcessLookupError
    (ESRCH) means the process is gone.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_pid(pid_file: Path) -> int | None:
    """Read the PID from the PID file. Returns None if absent or corrupt."""
    if not pid_file.exists():
        return None
    try:
        data = json.loads(pid_file.read_text())
        return int(data["pid"])
    except (KeyError, ValueError, json.JSONDecodeError, OSError):
        return None


def write_pid(pid_file: Path, pid: int, origin: str, session_id: str) -> None:
    """Write daemon identity to the PID file as JSON."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": pid,
        "origin": origin,
        "session_id": session_id,
        "started_at": datetime.now(UTC).isoformat(),
    }
    pid_file.write_text(json.dumps(payload))


@contextlib.contextmanager
def acquire_start_lock(lock_path: Path):
    """Exclusive non-blocking advisory flock on lock_path.

    Raises BlockingIOError if the lock is already held by another process.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def spawn_detached(cmd: list[str], env: dict | None = None) -> int:
    """Launch cmd in a fully detached child process.

    Uses start_new_session=True (setsid) so the child gets its own session and
    survives the parent exiting and any controlling-terminal SIGHUP. Returns
    the child PID. This is not the classic POSIX double-fork — setsid achieves
    the same detachment in a single step for modern Linux/macOS.
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    return proc.pid
