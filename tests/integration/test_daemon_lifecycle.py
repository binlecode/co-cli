"""Integration test: dream daemon process lifecycle.

Verifies start_daemon → PID file written → process live → stop_daemon → process dead.
Forks real OS processes.
POSIX-only (double-fork, Unix sockets).

Reload pattern: co_cli.daemons.dream.process imports DREAM_PID_FILE etc. from
co_cli.config.core at module level. Both modules must be reloaded after setting
CO_HOME so their path constants align with the tmp_path.

Unix socket path limit: macOS limits Unix socket paths to 104 bytes. The pytest
tmp_path is too long for the socket path, so tests that use the daemon socket
create their own shorter temp dir via tempfile.mkdtemp() as CO_HOME.

Note on stop: the daemon loop only exits via the IPC socket STOP command.
SIGTERM alone does not interrupt the asyncio receive_one() poll. stop_daemon
without force=True sends STOP via socket and is the correct shutdown path.
"""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
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


def _setup_co_home(co_home: Path) -> tuple:
    """Set CO_HOME, reload config.core and daemons.dream.process, return (core_mod, process_mod)."""
    os.environ["CO_HOME"] = str(co_home)
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)
    return core_mod, process_mod


def _short_co_home() -> Path:
    """Create a short temp dir suitable for Unix socket paths (< 104 chars on macOS).

    pytest's tmp_path generates paths that are too long for the macOS Unix socket
    path limit (104 bytes). We use mkdtemp() for tests that exercise the socket.
    """
    return Path(tempfile.mkdtemp(prefix="co-"))


def _read_pid_from_file(pid_file: Path) -> int | None:
    """Read PID from the daemon PID JSON file."""
    if not pid_file.exists():
        return None
    try:
        data = json.loads(pid_file.read_text())
        return int(data["pid"])
    except (KeyError, ValueError, json.JSONDecodeError, OSError):
        return None


def _wait_for_path(path: Path, *, timeout: float = 10.0, interval: float = 0.1) -> bool:
    """Poll until path exists or timeout expires. Returns True when found."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(interval)
    return False


def _wait_for_path_gone(path: Path, *, timeout: float = 10.0, interval: float = 0.1) -> bool:
    """Poll until path disappears or timeout expires. Returns True when gone."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not path.exists():
            return True
        time.sleep(interval)
    return False


def _is_pid_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def test_start_daemon_writes_pid_file_and_process_is_live(tmp_path: Path) -> None:
    """start_daemon (foreground=False) writes a PID file and the process is live.

    This test only checks the PID file — not the socket — so tmp_path is fine.
    """
    core_mod, process_mod = _setup_co_home(tmp_path)

    pid: int | None = None
    try:
        process_mod.start_daemon(tmp_path)

        pid_file = core_mod.DREAM_PID_FILE
        found = _wait_for_path(pid_file, timeout=10.0)
        assert found, f"PID file not created within timeout: {pid_file}"

        pid = _read_pid_from_file(pid_file)
        assert pid is not None, "PID file exists but could not read PID"
        assert _is_pid_live(pid), f"Process {pid} should be live after start_daemon"
    finally:
        if pid is not None and _is_pid_live(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def test_stop_daemon_via_socket_terminates_process() -> None:
    """Daemon spawned directly → socket appears → stop_daemon sends STOP → daemon exits.

    Uses a short CO_HOME path (via tempfile.mkdtemp) to stay within the macOS
    Unix socket path limit of 104 bytes.

    Verifies:
    1. The Unix socket appears (daemon is ready to accept commands).
    2. stop_daemon(force=False) sends STOP via IPC; the daemon breaks its loop.
    3. The PID file is removed by the daemon's own cleanup.
    4. The process is dead.
    """
    co_home = _short_co_home()
    core_mod, process_mod = _setup_co_home(co_home)

    stderr_log = co_home / "daemon_stderr.log"
    proc: subprocess.Popen | None = None
    stderr_fh = stderr_log.open("w")
    try:
        proc = subprocess.Popen(
            ["co", "dream", "start", "--foreground", "--origin=test", "--session-id=test"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_fh,
            start_new_session=True,
            env=dict(os.environ),
        )

        pid_file = core_mod.DREAM_PID_FILE
        pid_found = _wait_for_path(pid_file, timeout=10.0)
        if not pid_found:
            ret = proc.poll()
            stderr_text = stderr_log.read_text() if stderr_log.exists() else "(no stderr)"
            pytest.fail(
                f"PID file not created within timeout. "
                f"Process exited with {ret}. Stderr: {stderr_text[:500]}"
            )

        sock_path = core_mod.DREAM_SOCK
        sock_found = _wait_for_path(sock_path, timeout=15.0)
        if not sock_found:
            ret = proc.poll()
            stderr_text = stderr_log.read_text() if stderr_log.exists() else "(no stderr)"
            pytest.fail(
                f"Daemon socket never appeared at {sock_path}. "
                f"Process exited with {ret}. Stderr: {stderr_text[:500]}"
            )

        # stop_daemon without force uses the IPC socket STOP command
        process_mod.stop_daemon(co_home, force=False)

        # After clean shutdown the daemon unlinks the PID file
        pid_gone = _wait_for_path_gone(pid_file, timeout=10.0)
        assert pid_gone, "PID file should be removed after clean daemon shutdown"

        # Process should be dead
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)
        assert proc.poll() is not None, "Daemon process should have exited after STOP"
        proc = None  # Consumed
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
