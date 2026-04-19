"""Functional tests for the terminal display frontend."""

from tests._frontend import SilentFrontend

from co_cli.context.tool_approvals import resolve_approval_subject
from co_cli.display._core import QuestionPrompt, TerminalFrontend, console
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


def test_build_approval_panel_includes_tool_name_and_preview() -> None:
    """Approval panel contains tool_name as title and preview content when present."""
    subject = resolve_approval_subject(
        "write_file",
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

    assert "write_file" in output
    assert "def hello" in output


def test_build_approval_panel_no_preview_block_when_preview_is_none() -> None:
    """Approval panel renders only display text when preview is None — no separator shown."""
    subject = resolve_approval_subject(
        "run_shell_command",
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

    assert "run_shell_command" in output
    assert "git status" in output


def test_silent_frontend_prompt_question_returns_configured_answer_free_text() -> None:
    """SilentFrontend.prompt_question returns the configured answer for a free-text question."""
    frontend = SilentFrontend(question_answer="purple")
    result = frontend.prompt_question(QuestionPrompt(question="What is your favourite colour?"))
    assert result == "purple"


def test_silent_frontend_prompt_question_returns_configured_answer_constrained() -> None:
    """SilentFrontend.prompt_question returns the configured answer for a constrained question."""
    frontend = SilentFrontend(question_answer="yes")
    result = frontend.prompt_question(QuestionPrompt(question="Continue?", options=["yes", "no"]))
    assert result == "yes"


def test_silent_frontend_prompt_question_records_last_question() -> None:
    """SilentFrontend.prompt_question records the prompt so callers can inspect it."""
    frontend = SilentFrontend(question_answer="42")
    q = QuestionPrompt(question="What is the answer?", options=None)
    frontend.prompt_question(q)
    assert frontend.last_question is q
