"""Integration test: auto-spawn race — only one daemon spawns under concurrent attempts.

Tests:
1. Sequential idempotency: second autospawn call is a no-op when daemon is live.
2. Lock-hold prevention: when the advisory flock is held, autospawn skips spawn.
POSIX-only (fcntl.flock, double-fork, Unix sockets).

Note: fcntl.flock has process-level semantics — two threads in the same process
share the file-descriptor table and would not contend on the same open FD. Tests
here use a lock-hold fixture (holds the flock from the same process) to verify
the BlockingIOError path, and a sequential second-call test to verify PID-live
idempotency. A full cross-process race is covered by the daemon lifecycle tests.
"""

from __future__ import annotations

import importlib
import os
import signal
import sys
import tempfile
import time
from collections.abc import Generator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")


@pytest.fixture(autouse=True)
def _restore_co_home() -> Generator[None, None, None]:
    original = os.environ.get("CO_HOME")
    yield
    if original is None:
        os.environ.pop("CO_HOME", None)
    else:
        os.environ["CO_HOME"] = original
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)


def _short_co_home() -> Path:
    return Path(tempfile.mkdtemp(prefix="co-"))


def _setup_co_home(co_home: Path) -> tuple:
    os.environ["CO_HOME"] = str(co_home)
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)
    return core_mod, process_mod


def _read_pid(pid_file: Path) -> int | None:
    import json

    if not pid_file.exists():
        return None
    try:
        data = json.loads(pid_file.read_text())
        return int(data["pid"])
    except (KeyError, ValueError, json.JSONDecodeError, OSError):
        return None


def _is_pid_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _wait_for_path(path: Path, *, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(interval)
    return False


def test_second_autospawn_is_noop_when_daemon_live() -> None:
    """Calling start_daemon twice leaves exactly one running process.

    The second call reads the PID file, detects a live process, and returns
    without spawning. The PID recorded in the PID file is unchanged.
    """
    co_home = _short_co_home()
    core_mod, process_mod = _setup_co_home(co_home)

    pid: int | None = None
    try:
        process_mod.start_daemon(co_home, origin="test-run-1", session_id="s1")
        pid_file = core_mod.DREAM_PID_FILE
        assert _wait_for_path(pid_file, timeout=10.0), "PID file not created after first spawn"

        pid = _read_pid(pid_file)
        assert pid is not None, "PID not readable after first spawn"
        assert _is_pid_live(pid), "daemon not live after first spawn"

        process_mod.start_daemon(co_home, origin="test-run-2", session_id="s2")

        pid_after = _read_pid(pid_file)
        assert pid_after == pid, "second spawn must not replace the running daemon's PID"
        assert _is_pid_live(pid), "original daemon must still be live"
    finally:
        if pid is not None and _is_pid_live(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def test_autospawn_skipped_while_lock_held() -> None:
    """Attempting to start the daemon while the advisory lock is held raises no exception.

    maybe_autospawn_dream catches BlockingIOError from acquire_start_lock and
    returns silently. No daemon is spawned, no PID file is written.
    """
    co_home = _short_co_home()
    core_mod, process_mod = _setup_co_home(co_home)

    from co_cli.daemons.dream.process import acquire_start_lock

    lock_path = core_mod.DREAM_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    pid_file = core_mod.DREAM_PID_FILE

    with acquire_start_lock(lock_path):
        process_mod.start_daemon(co_home, origin="test-lock", session_id="s3")

    assert not pid_file.exists(), (
        "no PID file should exist when lock was held during spawn attempt"
    )
