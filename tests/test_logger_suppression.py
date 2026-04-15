"""Tests that third-party logger suppression is applied on co_cli.main import."""

import os
import subprocess
import sys
from pathlib import Path


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Build isolated env so the subprocess does not read user machine config."""
    env = os.environ.copy()
    env["CO_CLI_HOME"] = str(tmp_path / "co-cli-home")
    env["PYTHONPATH"] = str(Path.cwd())
    return env


def test_third_party_loggers_suppressed_to_warning(tmp_path: Path) -> None:
    """Importing co_cli.main sets all listed third-party loggers to WARNING."""
    env = _base_env(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import co_cli.main, logging; "
                "assert logging.getLogger('openai').level == logging.WARNING, 'openai'; "
                "assert logging.getLogger('httpx').level == logging.WARNING, 'httpx'; "
                "assert logging.getLogger('anthropic').level == logging.WARNING, 'anthropic'; "
                "assert logging.getLogger('hpack').level == logging.WARNING, 'hpack'"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, (
        f"Logger suppression assertion failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )


def test_co_cli_loggers_not_suppressed(tmp_path: Path) -> None:
    """co_cli.* loggers must not be forced to WARNING by third-party suppression."""
    env = _base_env(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import co_cli.main, logging; "
                "assert logging.getLogger('co_cli').level == logging.NOTSET, "
                "'co_cli logger must not be forced to WARNING'"
            ),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, (
        f"co_cli logger check failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
