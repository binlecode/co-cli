"""Functional tests for the /dream slash dispatcher routing.

Covers the slash surface added on top of the existing detached process.py
control functions: start | stop | tidy | status. Real-spawn (no mocks) so the
routing is exercised against the actual daemon lifecycle, consistent with
tests/integration/test_daemon_lifecycle.py.

POSIX-only (the daemon uses double-fork + POSIX signals).

Reload pattern: co_cli.config.core and co_cli.daemons.dream.process resolve
path constants at import; both must be reloaded after CO_HOME is set so they
align with tmp_path.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import signal
import sys
import time
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest

from co_cli.commands.dream import handle_dream_slash
from co_cli.display.core import console

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


def _read_pid(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        return int(json.loads(pid_file.read_text())["pid"])
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


def _ctx(enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        deps=SimpleNamespace(config=SimpleNamespace(dream=SimpleNamespace(enabled=enabled)))
    )


def _run_slash(args: str, enabled: bool = True) -> str:
    """Invoke the slash handler and return its captured console output."""
    with console.capture() as cap:
        asyncio.run(handle_dream_slash(_ctx(enabled), args))
    return cap.get()


def test_slash_start_brings_daemon_up_then_stop_force_brings_it_down() -> None:
    """`/dream start` → running; `/dream stop force` → not running."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        core_mod, process_mod = _setup_co_home(Path(tmp))
        pid: int | None = None
        try:
            _run_slash("start")
            assert _wait_for_path(core_mod.DREAM_PID_FILE, timeout=10.0), "daemon did not start"
            assert process_mod.status_daemon(Path(tmp)).get("running") is True
            pid = _read_pid(core_mod.DREAM_PID_FILE)

            _run_slash("stop force")
            assert process_mod.status_daemon(Path(tmp)).get("running") is False
            pid = None
        finally:
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass


def test_slash_start_when_already_running_does_not_raise_systemexit() -> None:
    """A second `/dream start` must report already-running, not abort the turn."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        core_mod, process_mod = _setup_co_home(Path(tmp))
        pid: int | None = None
        try:
            _run_slash("start")
            assert _wait_for_path(core_mod.DREAM_PID_FILE, timeout=10.0)
            pid = _read_pid(core_mod.DREAM_PID_FILE)

            out = _run_slash("start")
            assert "already running" in out.lower()
            assert process_mod.status_daemon(Path(tmp)).get("running") is True
        finally:
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass


def test_slash_stop_without_force_is_a_noop_and_warns() -> None:
    """`/dream stop` (no force token) warns and leaves the daemon running."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        core_mod, process_mod = _setup_co_home(Path(tmp))
        pid: int | None = None
        try:
            _run_slash("start")
            assert _wait_for_path(core_mod.DREAM_PID_FILE, timeout=10.0)
            pid = _read_pid(core_mod.DREAM_PID_FILE)

            out = _run_slash("stop")
            assert "shared" in out.lower()
            assert process_mod.status_daemon(Path(tmp)).get("running") is True
        finally:
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass


def test_slash_tidy_requests_housekeeping_when_running() -> None:
    """`/dream tidy` takes the running branch (confirmation), not the down hint.

    The sentinel file itself is self-consuming — the running daemon's idle tick
    unlinks it the instant it appears (_loop.py:_maybe_housekeep) — so asserting
    the file persists would race the consumer. The observable routing effect is
    the confirmation message vs. the not-running hint.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        core_mod, _ = _setup_co_home(Path(tmp))
        pid: int | None = None
        try:
            _run_slash("start")
            assert _wait_for_path(core_mod.DREAM_PID_FILE, timeout=10.0)
            pid = _read_pid(core_mod.DREAM_PID_FILE)

            out = _run_slash("tidy")
            assert "housekeeping requested" in out.lower()
            assert "not running" not in out.lower()
        finally:
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass


def test_slash_tidy_when_down_hints_and_writes_no_sentinel(tmp_path: Path) -> None:
    """`/dream tidy` while the daemon is down emits the hint and writes no sentinel."""
    core_mod, _ = _setup_co_home(tmp_path)
    out = _run_slash("tidy")
    assert "/dream start" in out
    assert not core_mod.DREAM_TIDY_TAG.exists()


def test_slash_unknown_subcommand_emits_usage(tmp_path: Path) -> None:
    """An unrecognized subcommand emits the usage line and does nothing."""
    _setup_co_home(tmp_path)
    out = _run_slash("frobnicate")
    assert "usage" in out.lower()


def test_slash_bare_shows_status_when_down(tmp_path: Path) -> None:
    """Bare `/dream` produces the status block (daemon down here)."""
    _setup_co_home(tmp_path)
    out = _run_slash("", enabled=True)
    assert "not running" in out.lower()
