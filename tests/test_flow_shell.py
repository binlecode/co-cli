"""shell_exec pty=True output fidelity — real subprocess, no mocks.

Covers the pseudo-terminal output-fidelity path: isatty reports True under the
pty, the byte-for-byte non-pty path is unchanged, the pty path returns cleanly
on this platform (macOS EIO-EOF regression guard), and a pty timeout kills the
process group and surfaces partial output without waiting out the full sleep.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.deps import CoDeps
from co_cli.tools.shell.execute import shell_exec
from co_cli.tools.shell_backend import ShellBackend


def _make_shell_ctx(tmp_path: Path) -> RunContext[CoDeps]:
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        workspace_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )
    return RunContext(
        deps=deps,
        model=None,
        usage=RunUsage(),
        tool_name="shell_exec",
        tool_call_approved=True,
    )


@pytest.mark.asyncio
async def test_pty_true_makes_stdout_a_tty(tmp_path: Path) -> None:
    backend = ShellBackend()
    exit_code, output = await backend.run_command(
        "python3 -c 'import sys;print(sys.stdout.isatty())'", timeout=10, pty=True
    )
    assert exit_code == 0
    assert "True" in output


@pytest.mark.asyncio
async def test_pty_false_stdout_is_not_a_tty(tmp_path: Path) -> None:
    backend = ShellBackend()
    exit_code, output = await backend.run_command(
        "python3 -c 'import sys;print(sys.stdout.isatty())'", timeout=10, pty=False
    )
    assert exit_code == 0
    assert "False" in output


@pytest.mark.asyncio
async def test_pty_false_echo_exact_output(tmp_path: Path) -> None:
    """Non-pty path is byte-for-byte unchanged: exit 0, output 'hi\\n'."""
    backend = ShellBackend()
    exit_code, output = await backend.run_command("echo hi", timeout=10)
    assert exit_code == 0
    assert output == "hi\n"


@pytest.mark.asyncio
async def test_pty_true_returns_cleanly_on_this_platform(tmp_path: Path) -> None:
    """EIO-EOF regression guard: a normal pty run must return, not raise."""
    backend = ShellBackend()
    exit_code, output = await backend.run_command("echo hello-pty", timeout=10, pty=True)
    assert exit_code == 0
    assert "hello-pty" in output


@pytest.mark.asyncio
async def test_pty_true_timeout_kills_and_surfaces_partial(tmp_path: Path) -> None:
    """A pty run over timeout kills the process group fast and surfaces partial output."""
    backend = ShellBackend()
    start = time.monotonic()
    with pytest.raises(RuntimeError) as exc_info:
        async with asyncio.timeout(4):
            await backend.run_command("echo before-sleep; sleep 5", timeout=1, pty=True)
    elapsed = time.monotonic() - start
    assert elapsed < 4, f"timeout path did not kill promptly (took {elapsed:.1f}s)"
    message = str(exc_info.value)
    assert "timed out after 1s" in message
    assert "before-sleep" in message


@pytest.mark.asyncio
async def test_pty_true_timeout_maps_to_model_retry(tmp_path: Path) -> None:
    """Through the tool, a pty timeout maps the RuntimeError to ModelRetry."""
    from pydantic_ai import ModelRetry

    ctx = _make_shell_ctx(tmp_path)
    start = time.monotonic()
    with pytest.raises(ModelRetry):
        async with asyncio.timeout(4):
            await shell_exec(ctx, "sleep 5", timeout=1, pty=True)
    elapsed = time.monotonic() - start
    assert elapsed < 4, f"tool timeout path did not kill promptly (took {elapsed:.1f}s)"


@pytest.mark.asyncio
async def test_cancel_kills_process_group(tmp_path: Path) -> None:
    """Cancelling a run mid-command kills the child — no orphaned process keeps running.

    The command would create a marker file after a delay; a clean kill on cancel
    means the marker never appears (a leaked process would create it).
    """
    backend = ShellBackend()
    marker = tmp_path / "marker"
    task = asyncio.ensure_future(backend.run_command(f"sleep 2; touch {marker}", timeout=30))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.5)
    assert not marker.exists(), "cancelled command kept running — process was orphaned"


@pytest.mark.asyncio
async def test_cancel_kills_process_group_pty(tmp_path: Path) -> None:
    """Same orphan-on-cancel guard for the pty-backed path."""
    backend = ShellBackend()
    marker = tmp_path / "marker-pty"
    task = asyncio.ensure_future(
        backend.run_command(f"sleep 2; touch {marker}", timeout=30, pty=True)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.5)
    assert not marker.exists(), "cancelled pty command kept running — process was orphaned"


@pytest.mark.asyncio
async def test_cancel_escalates_to_sigkill_for_sigterm_ignoring_child(tmp_path: Path) -> None:
    """A child that ignores SIGTERM is still killed on cancel via SIGKILL escalation.

    Without the escalation, SIGTERM alone would leave it running to create the marker.
    """
    backend = ShellBackend()
    marker = tmp_path / "marker-ignore-term"
    script_file = tmp_path / "ignore_term.py"
    script_file.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(2)\n"
        f"open({str(marker)!r}, 'w').close()\n"
    )
    task = asyncio.ensure_future(backend.run_command(f"python3 {script_file}", timeout=30))
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.5)
    assert not marker.exists(), "SIGTERM-ignoring child survived cancel — no SIGKILL escalation"
