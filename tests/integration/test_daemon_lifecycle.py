"""Integration test: dream daemon process lifecycle.

Verifies start_daemon → PID file written → process live → stop_daemon → process dead.
Forks real OS processes.
POSIX-only (double-fork, POSIX signals).

Reload pattern: co_cli.daemons.dream.process imports DREAM_PID_FILE etc. from
co_cli.config.core at module level. Both modules must be reloaded after setting
CO_HOME so their path constants align with the tmp_path.
"""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
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
    os.environ["CO_HOME"] = str(co_home)
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)
    return core_mod, process_mod


def _read_pid_from_file(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        data = json.loads(pid_file.read_text())
        return int(data["pid"])
    except (KeyError, ValueError, json.JSONDecodeError, OSError):
        return None


def _wait_for_path(path: Path, *, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(interval)
    return False


def _wait_for_path_gone(path: Path, *, timeout: float = 10.0, interval: float = 0.1) -> bool:
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
    """start_daemon (foreground=False) writes a PID file and the process is live."""
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


def test_stop_daemon_terminates_process(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SIGTERM during cold bootstrap → daemon exits via its own clean path, promptly.

    The PID file is written before create_deps, so stop_daemon's SIGTERM lands
    mid-bootstrap — the exact window where a cold embedding backend used to leave
    the daemon deaf for ~10s until force-kill. The embedder is intentionally left
    cold (no warm-up): ensure_ollama_warm only warms the agent LLM, and warming the
    embedder here would mask this regression. After the fix the daemon races
    bootstrap against shutdown and exits cooperatively.

    Verifies the cooperative path (not SIGKILL escalation):
    1. Daemon starts and writes PID file.
    2. stop_daemon() reports "daemon stopped" — the clean branch, not "force-killed".
    3. It returns inside the grace window (so SIGKILL was never reached).
    4. PID file is removed and the process is dead.

    Uses the detached launcher (no --foreground) exactly as production does: the
    launcher exits after spawning, so the real daemon reparents to init, which
    reaps it the instant it os._exits. A --foreground daemon parented to this test
    process would instead linger as a zombie that os.kill(pid, 0) reads as alive,
    masking the clean exit.
    """
    core_mod, process_mod = _setup_co_home(tmp_path)

    daemon_pid: int | None = None
    try:
        launcher = subprocess.Popen(
            ["co", "dream", "start", "--origin=test", "--session-id=test"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(os.environ),
        )
        launcher.wait(timeout=10)

        pid_file = core_mod.DREAM_PID_FILE
        pid_found = _wait_for_path(pid_file, timeout=10.0)
        assert pid_found, "PID file not created within timeout"
        daemon_pid = _read_pid_from_file(pid_file)
        assert daemon_pid is not None, "PID file exists but could not read PID"

        t0 = time.monotonic()
        process_mod.stop_daemon(tmp_path)
        stop_elapsed = time.monotonic() - t0
        out = capsys.readouterr().out
        assert "daemon stopped" in out, (
            f"daemon must exit via cooperative shutdown, not SIGKILL escalation; got: {out!r}"
        )
        assert stop_elapsed < process_mod.STOP_GRACE_SECONDS, (
            f"clean shutdown must complete within the {process_mod.STOP_GRACE_SECONDS:g}s grace "
            f"window (pre-fix this took ~10s+); took {stop_elapsed:.1f}s"
        )

        pid_gone = _wait_for_path_gone(pid_file, timeout=5.0)
        assert pid_gone, "PID file should be removed after clean daemon shutdown"
        assert not _is_pid_live(daemon_pid), "Daemon process should be dead after clean shutdown"
        daemon_pid = None
    finally:
        if daemon_pid is not None and _is_pid_live(daemon_pid):
            try:
                os.kill(daemon_pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def test_start_daemon_singleton_second_call_exits_nonzero(tmp_path: Path) -> None:
    """Second start_daemon call while daemon is live exits non-zero (SystemExit(1))."""
    core_mod, process_mod = _setup_co_home(tmp_path)

    pid: int | None = None
    try:
        process_mod.start_daemon(tmp_path)

        pid_file = core_mod.DREAM_PID_FILE
        found = _wait_for_path(pid_file, timeout=10.0)
        assert found, "PID file not created within timeout"
        pid = _read_pid_from_file(pid_file)

        with pytest.raises(SystemExit) as exc_info:
            process_mod.start_daemon(tmp_path)
        assert exc_info.value.code != 0, "Second start must exit non-zero"

        assert pid is not None, "PID must be readable from file"
        assert _is_pid_live(pid), "Original daemon must still be alive after second start attempt"
    finally:
        if pid is not None and _is_pid_live(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def test_start_daemon_with_stale_pid_succeeds(tmp_path: Path) -> None:
    """start_daemon with a stale PID file succeeds and overwrites the stale file."""
    core_mod, process_mod = _setup_co_home(tmp_path)

    pid_file = core_mod.DREAM_PID_FILE
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(json.dumps({"pid": 99999999, "origin": "stale", "session_id": ""}))

    pid: int | None = None
    try:
        process_mod.start_daemon(tmp_path)

        found = _wait_for_path(pid_file, timeout=10.0)
        assert found, "PID file not (re)created after stale-pid start"

        pid = _read_pid_from_file(pid_file)
        assert pid is not None, "PID file must contain a valid PID"
        assert pid != 99999999, "PID file must be overwritten with real daemon PID"
        assert _is_pid_live(pid), f"Daemon process {pid} must be live"
    finally:
        if pid is not None and _is_pid_live(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
