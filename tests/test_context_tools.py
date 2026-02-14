"""Tests for context-loading tools (load_personality)."""

import asyncio
from dataclasses import dataclass
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
    """Loading all pieces returns substantial content with correct role."""
    result = _run(load_personality(_ctx(personality="jeff")))
    assert result["role"] == "jeff"
    assert "character" in result["pieces_loaded"]
    assert "style" in result["pieces_loaded"]
    assert len(result["display"]) > 200


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
