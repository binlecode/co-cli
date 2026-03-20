"""Functional regression tests for startup failure handling."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from co_cli.tools._background import _make_task_id


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build an isolated env so startup tests do not read user machine config."""
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg-data")
    env["PYTHONPATH"] = str(Path.cwd())
    return env


def test_chat_startup_failure_exits_cleanly_without_traceback(tmp_path: Path) -> None:
    """A blocked startup should show a user-facing error, not a Python traceback."""
    env = _base_env(tmp_path)
    env["LLM_PROVIDER"] = "gemini"
    env.pop("LLM_API_KEY", None)

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from co_cli.main import app; app()",
            "chat",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, "Startup with missing Gemini key must fail"
    assert "Traceback" not in combined, "Startup failure must be rendered cleanly"
    assert "LLM_API_KEY" in combined or "gemini" in combined.lower(), \
        "Startup failure must explain the missing provider credential"


def test_make_task_id_is_unique_within_same_second() -> None:
    """Two tasks started in the same second must not collide on the same task_id."""
    start_second = int(time.time())
    while int(time.time()) == start_second:
        time.sleep(0.001)

    tid1 = _make_task_id("sleep 1")
    tid2 = _make_task_id("sleep 2")

    assert tid1 != tid2, "Background task IDs must be unique even within the same second"
