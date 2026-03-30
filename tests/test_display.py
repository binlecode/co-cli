"""Functional tests for the terminal display frontend."""

from co_cli.display._core import TerminalFrontend
from co_cli.display._stream_renderer import StreamRenderer


def test_terminal_frontend_tool_progress_replaces_generic_status() -> None:
    """Tool progress must take ownership of the active surface after a generic status line."""
    frontend = TerminalFrontend()
    try:
        frontend.on_status("Co is thinking...")
        assert frontend.active_surface() == "status"

        frontend.on_tool_progress("cap1", "Doctor: checking provider and model availability...")

        assert frontend.active_surface() == "tool"
        assert frontend.active_tool_messages() == (
            "Doctor: checking provider and model availability...",
        )
    finally:
        frontend.cleanup()


def test_stream_renderer_summary_mode_no_emit_without_sentence_boundary() -> None:
    """Summary reducer must not emit progress when no sentence boundary exists yet."""
    frontend = TerminalFrontend()
    renderer = StreamRenderer(frontend, reasoning_display="summary")
    try:
        renderer.append_thinking("I am thinking about this but no sentence ends here")
        assert frontend.active_surface() == "none"
        assert frontend.active_status_text() is None
    finally:
        frontend.cleanup()


def test_stream_renderer_summary_mode_emits_last_complete_sentence() -> None:
    """Summary reducer must emit only the last complete sentence from the buffer."""
    frontend = TerminalFrontend()
    renderer = StreamRenderer(frontend, reasoning_display="summary")
    try:
        renderer.append_thinking("First sentence. Second sentence. Third sentence.")
        assert frontend.active_surface() == "status"
        assert frontend.active_status_text() == "Third sentence."
    finally:
        frontend.cleanup()


def test_stream_renderer_summary_mode_truncates_long_sentence() -> None:
    """Summary reducer must truncate sentences longer than 80 characters."""
    frontend = TerminalFrontend()
    renderer = StreamRenderer(frontend, reasoning_display="summary")
    try:
        renderer.append_thinking("A" * 100 + ".")
        text = frontend.active_status_text()
        assert text is not None
        assert len(text) <= 80
        assert text.endswith("...")
    finally:
        frontend.cleanup()


def test_stream_renderer_summary_mode_empty_buffer_emits_nothing() -> None:
    """Summary reducer must emit nothing for whitespace-only input."""
    frontend = TerminalFrontend()
    renderer = StreamRenderer(frontend, reasoning_display="summary")
    try:
        renderer.append_thinking("   \n\t  ")
        assert frontend.active_surface() == "none"
        assert frontend.active_status_text() is None
    finally:
        frontend.cleanup()
