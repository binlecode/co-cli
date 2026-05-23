"""Behavioral tests for the dream daemon's file-based status/stop surface.

Covers behavior NOT exercised by integration tests:
- acquire_start_lock contention (BlockingIOError when held)
- status_daemon for no-PID / stale-PID / live-PID branches
- stop_daemon's stale PID cleanup path

Process lifecycle (start/stop/singleton/stale-pid-overwrite) is covered
end-to-end by tests/integration/test_daemon_lifecycle.py via real subprocess.
"""

import fcntl
import importlib
import json
import os
from collections.abc import Generator
from pathlib import Path

import pytest

from co_cli.daemons.dream._process import acquire_start_lock, write_pid


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


def _setup_co_home(co_home: Path):
    os.environ["CO_HOME"] = str(co_home)
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)
    return core_mod, process_mod


def test_acquire_start_lock_raises_when_already_locked(tmp_path: Path) -> None:
    """acquire_start_lock raises BlockingIOError when the lock file is already held."""
    lock_path = tmp_path / "daemon.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError), acquire_start_lock(lock_path):
            pass
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_status_daemon_no_pid_file_returns_not_running(tmp_path: Path) -> None:
    """status_daemon returns running=False when no PID file exists."""
    _core_mod, process_mod = _setup_co_home(tmp_path)

    result = process_mod.status_daemon(tmp_path)

    assert result["running"] is False


def test_status_daemon_stale_pid_returns_not_running(tmp_path: Path) -> None:
    """status_daemon returns running=False when PID file points at a dead process."""
    core_mod, process_mod = _setup_co_home(tmp_path)
    pid_file = core_mod.DREAM_PID_FILE
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(json.dumps({"pid": 99999999, "origin": "test", "session_id": ""}))

    result = process_mod.status_daemon(tmp_path)

    assert result["running"] is False


def test_status_daemon_live_pid_returns_running(tmp_path: Path) -> None:
    """status_daemon returns running=True with full payload when PID is live."""
    core_mod, process_mod = _setup_co_home(tmp_path)
    pid_file = core_mod.DREAM_PID_FILE
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    write_pid(pid_file, os.getpid(), origin="test", session_id="sess-xyz")

    result = process_mod.status_daemon(tmp_path)

    assert result["running"] is True
    assert result["pid"] == os.getpid()
    assert result.get("spawn_origin") == "test"
    assert result.get("spawn_session_id") == "sess-xyz"


def test_stop_daemon_cleans_up_stale_pid_file(tmp_path: Path) -> None:
    """stop_daemon unlinks a stale PID file when the recorded PID is dead."""
    core_mod, process_mod = _setup_co_home(tmp_path)
    pid_file = core_mod.DREAM_PID_FILE
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(json.dumps({"pid": 99999999, "origin": "test", "session_id": ""}))

    process_mod.stop_daemon(tmp_path)

    assert not pid_file.exists(), "stale PID file must be removed by stop_daemon"
