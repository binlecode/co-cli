"""Functional tests for the terminal display frontend."""

from co_cli.display._core import TerminalFrontend, console
from co_cli.display._stream_renderer import StreamRenderer
from co_cli.tools.approvals import resolve_approval_subject


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


def test_build_approval_panel_includes_tool_name_and_preview() -> None:
    """Approval panel contains tool_name as title and preview content when present."""
    subject = resolve_approval_subject(
        "file_write",
        {"path": "src/foo.py", "content": "def hello():\n    return 'world'\n"},
    )
    assert subject.preview is not None

    frontend = TerminalFrontend()
    try:
        panel = frontend._build_approval_panel(subject)
        with console.capture() as capture:
            console.print(panel)
        output = capture.get()
    finally:
        frontend.cleanup()

    assert "file_write" in output
    assert "def hello" in output


def test_build_approval_panel_no_preview_block_when_preview_is_none() -> None:
    """Approval panel renders only display text when preview is None — no separator shown."""
    subject = resolve_approval_subject(
        "shell",
        {"cmd": "git status"},
    )
    assert subject.preview is None

    frontend = TerminalFrontend()
    try:
        panel = frontend._build_approval_panel(subject)
        with console.capture() as capture:
            console.print(panel)
        output = capture.get()
    finally:
        frontend.cleanup()

    assert "shell" in output
    assert "git status" in output
