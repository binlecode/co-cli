"""Tests for _build_window() tool context expansion and cursor-based delta extraction."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from tests._settings import make_settings

from co_cli.deps import CoDeps
from co_cli.memory._extractor import (
    _build_window,
    drain_pending_extraction,
    fire_and_forget_extraction,
)
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# Minimal silent frontend for fire-and-forget tests
# ---------------------------------------------------------------------------


class _SilentFrontend:
    """Silent Frontend for extraction tests — no terminal output."""

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_tool_start(self, tool_id: str, name: str, args_display: str) -> None:
        pass

    def on_tool_progress(self, tool_id: str, message: str) -> None:
        pass

    def on_tool_complete(self, tool_id: str, result: object) -> None:
        pass

    def on_status(self, message: str) -> None:
        pass

    def on_reasoning_progress(self, text: str) -> None:
        pass

    def on_final_output(self, text: str) -> None:
        pass

    def prompt_approval(self, description: str) -> str:
        return "n"

    def clear_status(self) -> None:
        pass

    def set_input_active(self, active: bool) -> None:
        pass

    def cleanup(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tool_call_part_appears_in_window() -> None:
    """ToolCallPart in ModelResponse must appear as 'Tool(...)' in window output."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="list the files")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="list_dir",
                    args='{"path": "/tmp"}',
                    tool_call_id="call-1",
                ),
            ],
            model_name="test-model",
        ),
    ]
    window = _build_window(messages)
    assert "Tool(list_dir)" in window


def test_tool_return_truncated_at_300() -> None:
    """ToolReturnPart with 400-char content must be truncated to 300 chars in the window."""
    long_content = "x" * 400
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    content=long_content,
                    tool_call_id="call-2",
                ),
            ]
        ),
    ]
    window = _build_window(messages)
    # The line is: "Tool result (read_file): " + content[:300]
    assert "Tool result (read_file):" in window
    result_line = next(line for line in window.splitlines() if "Tool result (read_file)" in line)
    # Content portion: everything after "Tool result (read_file): "
    prefix = "Tool result (read_file): "
    content_in_line = result_line[len(prefix) :]
    assert len(content_in_line) == 300


def test_large_read_tool_output_skipped() -> None:
    """ToolReturnPart matching Read-tool line-number prefix must be skipped."""
    # Read-tool prefix pattern: starts with "1→ "
    read_tool_content = "1\u2192 line content here"
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    content=read_tool_content,
                    tool_call_id="call-3",
                ),
            ]
        ),
    ]
    window = _build_window(messages)
    assert "Tool result (read_file)" not in window

    # Also test: >1000 chars with no sentence boundary in first 200 chars
    no_boundary_content = "a" * 1100
    messages_no_boundary = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="list_dir",
                    content=no_boundary_content,
                    tool_call_id="call-4",
                ),
            ]
        ),
    ]
    window_no_boundary = _build_window(messages_no_boundary)
    assert "Tool result (list_dir)" not in window_no_boundary


def test_cursor_excludes_messages_before_start() -> None:
    """Delta slice starting at cursor_start must not include messages before the cursor."""
    all_messages = [
        ModelRequest(parts=[UserPromptPart(content=f"question {idx}")]) for idx in range(5)
    ]
    # Set cursor to start at index 3 — delta is messages[3:]
    delta = all_messages[3:]
    window = _build_window(delta)

    # Only messages at index 3 and 4 should appear
    assert "question 3" in window
    assert "question 4" in window

    # Messages before the cursor must not appear
    assert "question 0" not in window
    assert "question 1" not in window
    assert "question 2" not in window


@pytest.mark.asyncio
async def test_last_extracted_idx_advances_on_success(tmp_path: Path) -> None:
    """fire_and_forget_extraction must advance last_extracted_message_idx on success."""
    from co_cli._model_factory import build_model

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    config = make_settings()
    llm_model = build_model(config.llm)
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        memory_dir=memory_dir,
        model=llm_model,
    )

    frontend = _SilentFrontend()

    # Build a minimal message list for extraction
    messages = [
        ModelRequest(parts=[UserPromptPart(content="I always prefer dark mode in all editors")]),
        ModelResponse(
            parts=[TextPart(content="Got it, I'll remember that.")],
            model_name="test-model",
        ),
    ]

    cursor_start = 0
    delta = messages[cursor_start:]

    fire_and_forget_extraction(delta, deps=deps, frontend=frontend, cursor_start=cursor_start)
    async with asyncio.timeout(30):
        await drain_pending_extraction(timeout_ms=25_000)

    # Cursor must have advanced to cursor_start + len(delta)
    assert deps.session.last_extracted_message_idx == cursor_start + len(delta)
