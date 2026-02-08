"""Functional tests for terminal display behavior."""

from rich.console import Console
from rich.text import Text

from co_cli import display


def _recording_console() -> Console:
    return Console(record=True, force_terminal=False, color_system=None, width=80)


def test_on_thinking_delta_uses_text_renderable_and_placeholder(monkeypatch):
    recording_console = _recording_console()
    monkeypatch.setattr(display, "console", recording_console)

    frontend = display.TerminalFrontend()
    try:
        frontend.on_thinking_delta("")
        assert frontend._thinking_live is not None

        renderable = frontend._thinking_live.get_renderable()
        assert isinstance(renderable, Text)
        assert renderable.plain == "..."

        frontend.on_thinking_delta("updated")
        renderable = frontend._thinking_live.get_renderable()
        assert isinstance(renderable, Text)
        assert renderable.plain == "updated"
    finally:
        frontend.cleanup()


def test_on_thinking_commit_prints_multiline_text_without_panel(monkeypatch):
    recording_console = _recording_console()
    monkeypatch.setattr(display, "console", recording_console)

    frontend = display.TerminalFrontend()
    try:
        frontend.on_thinking_delta("draft")
        frontend.on_thinking_commit("line one\nline two")

        assert frontend._thinking_live is None
        assert recording_console.export_text() == "line one\nline two\n"
    finally:
        frontend.cleanup()
