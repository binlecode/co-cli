"""Behavioral tests for the dream daemon process management helpers.

Verifies: is_pid_live for live and nonexistent PIDs, read_pid returns None
when file is absent, write_pid + read_pid round-trip, and acquire_start_lock
exclusive locking semantics.
No LLM, no network — OS primitives only.
"""

import fcntl
import os
from pathlib import Path

import pytest

from co_cli.daemons.dream._process import (
    acquire_start_lock,
    is_pid_live,
    read_pid,
    write_pid,
)


def test_is_pid_live_returns_true_for_current_process() -> None:
    """is_pid_live must return True for the running process itself."""
    assert is_pid_live(os.getpid()) is True


def test_is_pid_live_returns_false_for_nonexistent_pid() -> None:
    """is_pid_live must return False for a PID that cannot exist."""
    assert is_pid_live(99999999) is False


def test_read_pid_returns_none_when_file_missing(tmp_path: Path) -> None:
    """read_pid must return None when the PID file does not exist."""
    result = read_pid(tmp_path / "daemon.pid")

    assert result is None


def test_write_pid_then_read_pid_round_trip(tmp_path: Path) -> None:
    """write_pid followed by read_pid must return the same pid."""
    pid_file = tmp_path / "daemon.pid"
    pid = os.getpid()

    write_pid(pid_file, pid, origin="test", session_id="sess-abc")
    result = read_pid(pid_file)

    assert result == pid


def test_acquire_start_lock_succeeds_when_no_contention(tmp_path: Path) -> None:
    """acquire_start_lock context manager acquires without raising when no other holder."""
    lock_path = tmp_path / "daemon.lock"

    with acquire_start_lock(lock_path):
        assert lock_path.exists()


def test_acquire_start_lock_raises_when_already_locked(tmp_path: Path) -> None:
    """acquire_start_lock raises BlockingIOError when the lock file is already held."""
    lock_path = tmp_path / "daemon.lock"
    # Open and flock the lock file ourselves to simulate an existing holder.
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError), acquire_start_lock(lock_path):
            pass
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
