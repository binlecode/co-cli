"""Tests for context-loading tools (load_personality)."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from co_cli.tools.context import load_personality


@dataclass
class _FakeDeps:
    personality: str | None = None


class _FakeRunContext:
    def __init__(self, deps: Any):
        self._deps = deps

    @property
    def deps(self) -> Any:
        return self._deps


def _ctx(personality: str | None = None) -> _FakeRunContext:
    return _FakeRunContext(_FakeDeps(personality=personality))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_load_personality_all_pieces():
    """Loading all axes returns substantial content with calibration examples."""
    result = _run(load_personality(_ctx(personality="jeff")))
    assert result["preset"] == "jeff"
    assert "character" in result["pieces_loaded"]
    assert "style" in result["pieces_loaded"]
    assert len(result["display"]) > 200
    assert "Calibration" in result["display"]


def test_load_personality_no_preset():
    """No personality configured → informative message."""
    result = _run(load_personality(_ctx(personality=None)))
    assert result["preset"] is None
    assert result["pieces_loaded"] == []
    assert "No personality" in result["display"]


def test_load_personality_invalid_piece():
    """Invalid axis name → error listing available axes."""
    result = _run(load_personality(_ctx(personality="jeff"), pieces=["nonexistent"]))
    assert result["pieces_loaded"] == []
    assert "Unknown" in result["display"]


def test_load_personality_precedence_note():
    """When both character + style axes loaded, output includes precedence rule."""
    result = _run(load_personality(_ctx(personality="jeff")))
    assert len(result["pieces_loaded"]) == 2
    assert "Override precedence" in result["display"]


def test_load_personality_no_precedence_for_style_only():
    """When only style axis loaded, no precedence note needed."""
    result = _run(load_personality(_ctx(personality="terse")))
    assert "Override precedence" not in result["display"]


def test_load_personality_with_personality_memories(tmp_path, monkeypatch):
    """Personality-context memories appear as Learned Context in output."""
    memory_dir = tmp_path / ".co-cli" / "knowledge" / "memories"
    memory_dir.mkdir(parents=True)
    memory_file = memory_dir / "001-user-prefers-direct.md"
    memory_file.write_text(
        "---\n"
        "id: 1\n"
        "created: '2026-01-15T00:00:00+00:00'\n"
        "tags:\n"
        "  - preference\n"
        "  - personality-context\n"
        "---\n\n"
        "User prefers direct answers without hedging\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "cwd", staticmethod(lambda: tmp_path))
    result = _run(load_personality(_ctx(personality="jeff")))
    assert "Learned Context" in result["display"]
    assert "User prefers direct answers without hedging" in result["display"]
