"""Integration test: maybe_autospawn_dream emits first-spawn notice and creates PID file.

Verifies that when dream.autostart=True and no daemon is running, maybe_autospawn_dream:
  1. Calls frontend.on_status with the first-spawn notice text.
  2. Forks a real daemon process (PID file appears within timeout).

POSIX-only. Uses a short CO_HOME path (tempfile.mkdtemp) to stay within the
macOS Unix socket path limit of 104 bytes. Teardown kills any spawned process.
"""

from __future__ import annotations

import importlib
import json
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
    original_no_autospawn = os.environ.get("CO_DREAM_NO_AUTOSPAWN")
    yield
    if original is None:
        os.environ.pop("CO_HOME", None)
    else:
        os.environ["CO_HOME"] = original
    if original_no_autospawn is None:
        os.environ.pop("CO_DREAM_NO_AUTOSPAWN", None)
    else:
        os.environ["CO_DREAM_NO_AUTOSPAWN"] = original_no_autospawn
    import co_cli.config.core as core_mod

    importlib.reload(core_mod)


def _short_co_home() -> Path:
    """Create a short temp dir suitable for Unix socket paths (< 104 chars on macOS)."""
    return Path(tempfile.mkdtemp(prefix="co-"))


class _CaptureFrontend:
    """Minimal frontend that records on_status calls."""

    def __init__(self) -> None:
        self.statuses: list[str] = []

    def on_status(self, msg: str) -> None:
        self.statuses.append(msg)


def _read_pid_from_file(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        data = json.loads(pid_file.read_text())
        return int(data["pid"])
    except (KeyError, ValueError, json.JSONDecodeError, OSError):
        return None


def _wait_for_pid_file(pid_file: Path, *, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_file.exists():
            return True
        time.sleep(interval)
    return False


def _is_pid_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def test_autospawn_emits_notice_and_creates_pid_file() -> None:
    """maybe_autospawn_dream with dream.autostart=True emits first-spawn notice and PID file."""
    co_home = _short_co_home()
    os.environ["CO_HOME"] = str(co_home)
    # Ensure autospawn opt-out is not set
    os.environ.pop("CO_DREAM_NO_AUTOSPAWN", None)

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)

    from tests._settings import SETTINGS_NO_MCP

    from co_cli.bootstrap.core import maybe_autospawn_dream
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"dream": SETTINGS_NO_MCP.dream.model_copy(update={"autostart": True})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config)
    session_file = co_home / "sessions" / "autospawn-session.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    deps.session.session_path = session_file

    frontend = _CaptureFrontend()
    pid: int | None = None
    try:
        maybe_autospawn_dream(deps, frontend)

        # First-spawn notice must have been emitted
        assert any("[dream]" in msg for msg in frontend.statuses), (
            f"Expected '[dream]' notice in statuses, got: {frontend.statuses}"
        )

        # PID file must appear (daemon was forked)
        pid_file = core_mod.DREAM_PID_FILE
        found = _wait_for_pid_file(pid_file, timeout=10.0)
        assert found, "PID file not created by autospawn within timeout"

        pid = _read_pid_from_file(pid_file)
        assert pid is not None, "PID file exists but could not read PID"
    finally:
        if pid is not None and _is_pid_live(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass


def test_autospawn_no_op_when_disabled(tmp_path: Path) -> None:
    """maybe_autospawn_dream with dream.autostart=False emits no notice and no PID file."""
    os.environ["CO_HOME"] = str(tmp_path)
    os.environ.pop("CO_DREAM_NO_AUTOSPAWN", None)

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)

    from tests._settings import SETTINGS_NO_MCP

    from co_cli.bootstrap.core import maybe_autospawn_dream
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"dream": SETTINGS_NO_MCP.dream.model_copy(update={"autostart": False})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config)
    session_file = tmp_path / "sessions" / "disabled-session.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    deps.session.session_path = session_file

    frontend = _CaptureFrontend()
    maybe_autospawn_dream(deps, frontend)

    assert frontend.statuses == [], "No notice expected when dream.autostart=False"
    pid_file = core_mod.DREAM_PID_FILE
    assert not pid_file.exists(), "No PID file expected when dream.autostart=False"


def test_autospawn_no_op_when_opt_out_env_set(tmp_path: Path) -> None:
    """CO_DREAM_NO_AUTOSPAWN set → no spawn, no notice."""
    os.environ["CO_HOME"] = str(tmp_path)
    os.environ["CO_DREAM_NO_AUTOSPAWN"] = "1"

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)

    from tests._settings import SETTINGS_NO_MCP

    from co_cli.bootstrap.core import maybe_autospawn_dream
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"dream": SETTINGS_NO_MCP.dream.model_copy(update={"autostart": True})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config)
    session_file = tmp_path / "sessions" / "optout-session.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    deps.session.session_path = session_file

    frontend = _CaptureFrontend()
    maybe_autospawn_dream(deps, frontend)

    assert frontend.statuses == [], "No notice expected when CO_DREAM_NO_AUTOSPAWN is set"
    pid_file = core_mod.DREAM_PID_FILE
    assert not pid_file.exists(), "No PID file expected with CO_DREAM_NO_AUTOSPAWN"
