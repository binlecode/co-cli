"""Tests for context-loading tools (load_aspect, load_personality)."""

import asyncio
from dataclasses import dataclass
from typing import Any

from co_cli.tools.context import load_aspect, load_personality


# ---------------------------------------------------------------------------
# Helpers — lightweight RunContext stand-in
# ---------------------------------------------------------------------------


@dataclass
class _FakeDeps:
    """Minimal deps for context tool tests."""

    personality: str | None = None


class _FakeRunContext:
    """Minimal RunContext stand-in for testing tools without a full agent."""

    def __init__(self, deps: Any):
        self._deps = deps

    @property
    def deps(self) -> Any:
        return self._deps


def _ctx(personality: str | None = None) -> _FakeRunContext:
    return _FakeRunContext(_FakeDeps(personality=personality))


def _run(coro):
    """Run async coroutine in sync test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# load_personality — content verification
# ---------------------------------------------------------------------------


def test_load_personality_jeff_character():
    """Jeff character piece contains robot identity traits."""
    result = _run(load_personality(_ctx(personality="jeff"), pieces=["character"]))
    assert result["role"] == "jeff"
    assert result["pieces_loaded"] == ["character"]
    display = result["display"]
    # Jeff character file content
    assert "Jeff" in display
    assert "robot" in display.lower() or "72%" in display


def test_load_personality_jeff_style():
    """Jeff style piece (warm) contains warm communication style."""
    result = _run(load_personality(_ctx(personality="jeff"), pieces=["style"]))
    assert result["role"] == "jeff"
    assert result["pieces_loaded"] == ["style"]
    display = result["display"]
    # Warm style file content
    assert "Warm" in display or "warm" in display
    assert "Collaborative" in display or "collaborative" in display


def test_load_personality_jeff_role():
    """Jeff role piece contains full role description with examples."""
    result = _run(load_personality(_ctx(personality="jeff"), pieces=["role"]))
    assert result["role"] == "jeff"
    assert result["pieces_loaded"] == ["role"]
    display = result["display"]
    # Jeff role file has extensive content
    assert "Jeff" in display
    assert "Communication Style" in display


def test_load_personality_jeff_all_pieces():
    """Jeff with no pieces → loads character + style + role."""
    result = _run(load_personality(_ctx(personality="jeff")))
    assert result["role"] == "jeff"
    # Jeff has character, style, and role pieces
    assert "character" in result["pieces_loaded"]
    assert "style" in result["pieces_loaded"]
    assert "role" in result["pieces_loaded"]
    display = result["display"]
    # Combined content should have character + style + role content
    assert "Jeff" in display
    assert len(display) > 200  # substantial content


def test_load_personality_finch_character():
    """Finch character piece contains mentor identity."""
    result = _run(load_personality(_ctx(personality="finch"), pieces=["character"]))
    assert result["role"] == "finch"
    display = result["display"]
    assert "Finch" in display
    assert "mentor" in display.lower() or "teaching" in display.lower()


def test_load_personality_terse_no_character():
    """Terse role has no character, only style."""
    result = _run(load_personality(_ctx(personality="terse")))
    assert result["role"] == "terse"
    assert "character" not in result["pieces_loaded"]
    assert "style" in result["pieces_loaded"]
    display = result["display"]
    # Terse style content
    assert "minimal" in display.lower() or "terse" in display.lower()


def test_load_personality_no_preset():
    """No personality configured → informative message."""
    result = _run(load_personality(_ctx(personality=None)))
    assert result["role"] is None
    assert result["pieces_loaded"] == []
    assert "No personality" in result["display"]


def test_load_personality_invalid_piece():
    """Invalid piece name → error listing available pieces."""
    result = _run(load_personality(_ctx(personality="jeff"), pieces=["nonexistent"]))
    assert result["pieces_loaded"] == []
    assert "Unknown" in result["display"]
    assert "character" in result["display"]  # lists available pieces


# ---------------------------------------------------------------------------
# load_aspect — situational guidance
# ---------------------------------------------------------------------------


def test_load_aspect_all():
    """No names → loads all available aspects."""
    result = _run(load_aspect(_ctx(), names=None))
    assert result["count"] >= 3
    assert "debugging" in result["aspects_loaded"]
    assert "planning" in result["aspects_loaded"]
    assert "code_review" in result["aspects_loaded"]
    assert len(result["display"]) > 100


def test_load_aspect_single():
    """Single name → loads only that aspect."""
    result = _run(load_aspect(_ctx(), names=["debugging"]))
    assert result["aspects_loaded"] == ["debugging"]
    assert result["count"] == 1
    assert "debugging" in result["display"].lower() or "symptom" in result["display"].lower()


def test_load_aspect_multiple():
    """Multiple names → loads requested aspects in order."""
    result = _run(load_aspect(_ctx(), names=["planning", "code_review"]))
    assert result["aspects_loaded"] == ["planning", "code_review"]
    assert result["count"] == 2


def test_load_aspect_invalid_name():
    """Invalid name → error listing available aspects."""
    result = _run(load_aspect(_ctx(), names=["nonexistent"]))
    assert result["aspects_loaded"] == []
    assert result["count"] == 0
    assert "Unknown" in result["display"]
    assert "debugging" in result["display"]  # lists available


def test_load_aspect_empty_dir(tmp_path, monkeypatch):
    """Empty aspects directory → informative message."""
    empty_dir = tmp_path / "aspects"
    empty_dir.mkdir()
    monkeypatch.setattr("co_cli.tools.context._ASPECTS_DIR", empty_dir)
    result = _run(load_aspect(_ctx(), names=None))
    assert result["aspects_loaded"] == []
    assert result["count"] == 0
    assert "No aspects" in result["display"]


def test_load_aspect_missing_dir(tmp_path, monkeypatch):
    """Missing aspects directory → informative message."""
    monkeypatch.setattr("co_cli.tools.context._ASPECTS_DIR", tmp_path / "nonexistent")
    result = _run(load_aspect(_ctx(), names=None))
    assert result["aspects_loaded"] == []
    assert result["count"] == 0
    assert "No aspects" in result["display"]

