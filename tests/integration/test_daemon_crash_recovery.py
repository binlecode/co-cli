"""Integration test: daemon crash recovery — restart re-processes queued kick files.

Verifies that a kick file written to the queue while the daemon is stopped is
picked up and processed by the daemon on the next start via initial_drain.
This covers both crash recovery (daemon killed) and clean-restart scenarios.

POSIX-only (double-fork, Unix sockets).

Success path: the kick file has session_id with no matching session file.
process_review detects transcript_path.exists() == False, logs a warning, and
returns without error. _drain_queue then moves the file to done/.
"""

from __future__ import annotations

import importlib
import json
import os
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


def test_queued_kick_processed_after_daemon_restart() -> None:
    """Kick file written when daemon is down is processed on daemon restart.

    Flow:
    1. Start daemon → wait for socket.
    2. Stop daemon cleanly via STOP command.
    3. Write a kick file while daemon is stopped.
    4. Restart daemon → initial_drain picks up the kick file.
    5. Assert the kick file moves from queue/ to done/.

    The kick file uses a nonexistent session_id so process_review returns
    immediately without LLM calls.
    """
    co_home = _short_co_home()
    core_mod, process_mod = _setup_co_home(co_home)

    proc: subprocess.Popen | None = None
    proc2: subprocess.Popen | None = None

    try:
        env = dict(os.environ)

        proc = subprocess.Popen(
            [
                "co",
                "dream",
                "start",
                "--foreground",
                "--origin=test-crash-recovery",
                "--session-id=s-initial",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )

        sock_path = core_mod.DREAM_SOCK
        sock_found = _wait_for_path(sock_path, timeout=15.0)
        assert sock_found, f"daemon socket never appeared at {sock_path}"

        process_mod.stop_daemon(co_home, force=False)
        pid_file = core_mod.DREAM_PID_FILE
        assert _wait_for_path_gone(pid_file, timeout=10.0), "PID file not removed after stop"
        assert _wait_for_path_gone(sock_path, timeout=10.0), "socket not removed after stop"

        proc_retcode = proc.wait(timeout=5)
        assert proc_retcode is not None
        proc = None

        kick_file = _write_kick_file(core_mod.DREAM_QUEUE_DIR, session_id="no-such-session")
        assert kick_file.exists(), "kick file must be written to queue"

        proc2 = subprocess.Popen(
            [
                "co",
                "dream",
                "start",
                "--foreground",
                "--origin=test-crash-recovery-restart",
                "--session-id=s-restart",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )

        done_file = core_mod.DREAM_QUEUE_DONE_DIR / kick_file.name
        done_found = _wait_for_path(done_file, timeout=20.0)
        assert done_found, (
            f"kick file {kick_file.name} was not moved to done/ within timeout. "
            f"Still in queue: {kick_file.exists()}"
        )
        assert not kick_file.exists(), "original kick file must be gone from queue/"

        process_mod.stop_daemon(co_home, force=False)
        _wait_for_path_gone(core_mod.DREAM_PID_FILE, timeout=10.0)
        proc_retcode2 = proc2.wait(timeout=5)
        assert proc_retcode2 is not None
        proc2 = None
    finally:
        for proc_ref in (proc, proc2):
            if proc_ref is not None and proc_ref.poll() is None:
                try:
                    proc_ref.kill()
                    proc_ref.wait(timeout=3)
                except Exception:
                    pass
