"""Dream daemon process management helpers.

# POSIX-only: fcntl.flock, Unix sockets, double-fork detach.
# co-cli is darwin/linux-first; no Windows path is provided.
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
    """Return True if the process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


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


def double_fork_detach(cmd: list[str], env: dict | None = None) -> int:
    """Launch cmd in a fully detached child process via double-fork semantics.

    Uses start_new_session=True to create a new process group and session so the
    child survives the parent exiting. Returns the child PID.
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
