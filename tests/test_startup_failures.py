"""Functional regression tests for startup failure handling."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build an isolated env so startup tests do not read user machine config."""
    env = os.environ.copy()
    env["CO_CLI_HOME"] = str(tmp_path / "co-cli-home")
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

