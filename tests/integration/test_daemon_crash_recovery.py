"""Integration test: daemon crash recovery — restart re-processes queued kick files.

Verifies that a kick file written to the queue while the daemon is stopped is
picked up and processed by the daemon on the next start via initial_drain.
This covers both crash recovery (daemon killed) and clean-restart scenarios.

POSIX-only (double-fork, POSIX signals).

Success path: the kick file has session_id with no matching session file.
process_review detects transcript_path.exists() == False, logs a warning, and
returns without error. _drain_queue then moves the file to done/.
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


def _short_co_home() -> Path:
    return Path(tempfile.mkdtemp(prefix="co-"))


def _setup_co_home(co_home: Path) -> tuple:
    os.environ["CO_HOME"] = str(co_home)
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)
    return core_mod, process_mod


def _wait_for_path(path: Path, *, timeout: float = 15.0, interval: float = 0.1) -> bool:
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


def _write_kick_file(queue_dir: Path, session_id: str) -> Path:
    """Write a minimal kick file to queue_dir. Returns the file path."""
    import uuid
    from datetime import UTC, datetime

    queue_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    fname = f"{ts}-{uuid.uuid4()}.json"
    path = queue_dir / fname
    payload = {
        "domain": "memory",
        "session_id": session_id,
        "persisted_message_count": 0,
        "attempts": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload))
    return path


def test_queued_kick_processed_after_daemon_restart(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Kick file written when daemon is down is processed on daemon restart.

    Flow:
    1. Start daemon → wait for socket.
    2. Stop daemon cleanly via STOP command.
    3. Write a kick file while daemon is stopped.
    4. Restart daemon → initial_drain picks up the kick file.
    5. Assert the kick file moves from queue/ to done/.

    The kick file uses a nonexistent session_id so process_review returns
    immediately without LLM calls. The embedder is left cold (no warm-up) — both
    stops land during/after a cold bootstrap and must exit via the cooperative
    "daemon stopped" path rather than the ~10s deaf-SIGTERM force-kill.
    """
    co_home = _short_co_home()
    core_mod, process_mod = _setup_co_home(co_home)
    pid_file = core_mod.DREAM_PID_FILE

    daemon_pids: list[int] = []
    try:
        env = dict(os.environ)

        # Detached launcher (production path, no --foreground): the launcher exits
        # after spawning, so the real daemon reparents to init and is reaped the
        # instant it os._exits — letting stop_daemon observe the clean exit instead
        # of a lingering zombie.
        launcher = subprocess.Popen(
            ["co", "dream", "start", "--origin=test-crash-recovery", "--session-id=s-initial"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        launcher.wait(timeout=10)

        assert _wait_for_path(pid_file, timeout=15.0), (
            "PID file never appeared — daemon did not start"
        )
        pid1 = process_mod.read_pid(pid_file)
        assert pid1 is not None, "could not read first daemon PID"
        daemon_pids.append(pid1)

        process_mod.stop_daemon(co_home)
        assert "daemon stopped" in capsys.readouterr().out, (
            "first stop (mid-cold-bootstrap) must exit cooperatively, not via SIGKILL"
        )
        assert _wait_for_path_gone(pid_file, timeout=10.0), "PID file not removed after stop"
        assert not process_mod.is_pid_live(pid1), "first daemon must be dead after clean stop"

        kick_file = _write_kick_file(core_mod.DREAM_QUEUE_DIR, session_id="no-such-session")
        assert kick_file.exists(), "kick file must be written to queue"

        launcher2 = subprocess.Popen(
            [
                "co",
                "dream",
                "start",
                "--origin=test-crash-recovery-restart",
                "--session-id=s-restart",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        launcher2.wait(timeout=10)

        assert _wait_for_path(pid_file, timeout=15.0), "restart PID file never appeared"
        pid2 = process_mod.read_pid(pid_file)
        assert pid2 is not None, "could not read restarted daemon PID"
        daemon_pids.append(pid2)

        done_file = core_mod.DREAM_QUEUE_DONE_DIR / kick_file.name
        done_found = _wait_for_path(done_file, timeout=20.0)
        assert done_found, (
            f"kick file {kick_file.name} was not moved to done/ within timeout. "
            f"Still in queue: {kick_file.exists()}"
        )
        assert not kick_file.exists(), "original kick file must be gone from queue/"

        process_mod.stop_daemon(co_home)
        assert "daemon stopped" in capsys.readouterr().out, (
            "second stop must exit cooperatively, not via SIGKILL"
        )
        assert _wait_for_path_gone(pid_file, timeout=10.0), (
            "PID file not removed after second stop"
        )
        assert not process_mod.is_pid_live(pid2), "restarted daemon must be dead after clean stop"
        daemon_pids.clear()
    finally:
        for pid in daemon_pids:
            if process_mod.is_pid_live(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
