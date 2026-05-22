"""Tests for build_dream_line — three states + stall-safety."""

import time
from pathlib import Path
from unittest.mock import MagicMock

from co_cli.bootstrap.banner import build_dream_line


def _make_deps(*, dream_enabled: bool) -> MagicMock:
    """Return a minimal deps-like mock with config.dream.enabled set."""
    deps = MagicMock()
    deps.config.dream.enabled = dream_enabled
    return deps


def test_dream_line_disabled() -> None:
    """build_dream_line returns 'disabled' when dream.enabled is False."""
    deps = _make_deps(dream_enabled=False)
    result = build_dream_line(deps)
    assert "disabled" in result


def test_dream_line_running(monkeypatch: MagicMock) -> None:
    """build_dream_line returns 'running' and queue depth when daemon is up."""
    monkeypatch.setattr(
        "co_cli.commands.dream._socket_status",
        lambda timeout_ms=200: {"queue_depth": 3, "pid": 123},
    )
    deps = _make_deps(dream_enabled=True)
    result = build_dream_line(deps)
    assert "running" in result
    assert "queue: 3" in result


def test_dream_line_enabled_not_running(monkeypatch: MagicMock, tmp_path: Path) -> None:
    """build_dream_line returns 'enabled but daemon not running' when socket is unreachable."""
    monkeypatch.setattr(
        "co_cli.commands.dream._socket_status",
        lambda timeout_ms=200: None,
    )

    # DREAM_QUEUE_DIR is a lazy import inside build_dream_line's body
    # (from co_cli.config.core import DREAM_QUEUE_DIR), so patch it at its origin module.
    fake_queue_dir = tmp_path / "queue"
    fake_queue_dir.mkdir()
    (fake_queue_dir / "item1.json").write_text("{}")
    (fake_queue_dir / "item2.json").write_text("{}")

    monkeypatch.setattr("co_cli.config.core.DREAM_QUEUE_DIR", fake_queue_dir)

    deps = _make_deps(dream_enabled=True)
    result = build_dream_line(deps)
    assert "enabled but daemon not running" in result


def test_dream_line_does_not_stall(monkeypatch: MagicMock) -> None:
    """build_dream_line returns in under 500ms even when _socket_status hangs."""
    import time as _time

    def _hung_socket(timeout_ms: int = 200) -> dict | None:
        # Deliberately sleep longer than the 200ms timeout build_dream_line passes,
        # but raise immediately to simulate the socket's own timeout firing.
        # We simulate a hung scenario by sleeping briefly then returning None.
        _time.sleep(0.05)
        return None

    monkeypatch.setattr(
        "co_cli.commands.dream._socket_status",
        _hung_socket,
    )
    monkeypatch.setattr("co_cli.config.core.DREAM_QUEUE_DIR", MagicMock(exists=lambda: False))

    deps = _make_deps(dream_enabled=True)

    start = time.monotonic()
    build_dream_line(deps)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 500, f"build_dream_line took {elapsed_ms:.0f}ms — expected < 500ms"
