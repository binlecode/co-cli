"""Integration test: auto-spawn race — lock-hold prevents concurrent spawn.

POSIX-only (fcntl.flock, double-fork detach, POSIX signals).

fcntl.flock has process-level semantics — two threads in the same process
share the file-descriptor table and would not contend on the same open FD.
This test holds the flock from the same process to verify the BlockingIOError
path. The PID-live singleton-on-start contract is covered by
tests/integration/test_daemon_lifecycle.py::test_start_daemon_singleton_second_call_exits_nonzero.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
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
