"""Tests for handle_dream_slash — three rendering paths + no-subprocess invariant."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from co_cli.commands.dream import handle_dream_slash


def _make_ctx(*, dream_enabled: bool) -> MagicMock:
    """Return a minimal CommandContext-like mock with deps.config.dream.enabled set."""
    ctx = MagicMock()
    ctx.deps.config.dream.enabled = dream_enabled
    return ctx


def _patch_console(monkeypatch: MagicMock) -> list[str]:
    """Patch co_cli.display.core.console.print to capture output lines; return the list."""
    printed: list[str] = []
    import co_cli.display.core as _display_module

    monkeypatch.setattr(
        _display_module.console, "print", lambda *a, **kw: printed.append(str(a[0]))
    )
    return printed


@pytest.mark.asyncio
async def test_dream_slash_daemon_up(monkeypatch: MagicMock, tmp_path: Path) -> None:
    """Daemon running: output contains 'running' and key status fields; no Popen."""
    status_payload = {"running": True, "pid": 123, "queue_depth": 0, "uptime_seconds": 42}
    monkeypatch.setattr(
        "co_cli.commands.dream._socket_status",
        lambda timeout_ms=500: status_payload,
    )

    popen_calls: list = []

    def _spy_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return MagicMock()

    monkeypatch.setattr(subprocess, "Popen", _spy_popen)

    ctx = _make_ctx(dream_enabled=True)
    printed = _patch_console(monkeypatch)

    await handle_dream_slash(ctx, "")

    output = "\n".join(printed)
    assert "running" in output
    assert "pid" in output
    assert popen_calls == [], "subprocess.Popen must not be called"


@pytest.mark.asyncio
async def test_dream_slash_enabled_not_running(monkeypatch: MagicMock, tmp_path: Path) -> None:
    """Daemon down but enabled: output mentions 'not running' and 'co dream start'."""
    monkeypatch.setattr(
        "co_cli.commands.dream._socket_status",
        lambda timeout_ms=500: None,
    )

    # Patch queue dir so glob does not touch the real filesystem
    fake_queue_dir = tmp_path / "queue"
    fake_queue_dir.mkdir()
    monkeypatch.setattr("co_cli.config.core.DREAM_QUEUE_DIR", fake_queue_dir)

    ctx = _make_ctx(dream_enabled=True)
    printed = _patch_console(monkeypatch)

    await handle_dream_slash(ctx, "")

    output = "\n".join(printed)
    assert "not running" in output
    assert "dream start" in output


@pytest.mark.asyncio
async def test_dream_slash_disabled(monkeypatch: MagicMock) -> None:
    """Daemon disabled: output mentions 'disabled'."""
    monkeypatch.setattr(
        "co_cli.commands.dream._socket_status",
        lambda timeout_ms=500: None,
    )

    ctx = _make_ctx(dream_enabled=False)
    printed = _patch_console(monkeypatch)

    await handle_dream_slash(ctx, "")

    output = "\n".join(printed)
    assert "disabled" in output


@pytest.mark.asyncio
async def test_dream_slash_never_spawns_process(monkeypatch: MagicMock, tmp_path: Path) -> None:
    """handle_dream_slash never calls subprocess.Popen regardless of daemon state."""
    monkeypatch.setattr(
        "co_cli.commands.dream._socket_status",
        lambda timeout_ms=500: None,
    )

    fake_queue_dir = tmp_path / "queue"
    fake_queue_dir.mkdir()
    monkeypatch.setattr("co_cli.config.core.DREAM_QUEUE_DIR", fake_queue_dir)

    popen_calls: list = []

    def _spy_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return MagicMock()

    monkeypatch.setattr(subprocess, "Popen", _spy_popen)

    ctx = _make_ctx(dream_enabled=True)
    _patch_console(monkeypatch)

    await handle_dream_slash(ctx, "")

    assert popen_calls == [], "subprocess.Popen must never be called by handle_dream_slash"
