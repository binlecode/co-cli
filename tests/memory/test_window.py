"""Tests for transcript window builder (build_transcript_window)."""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.memory._window import build_transcript_window


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
    window = build_transcript_window(messages)
    assert "Tool(list_dir)" in window


def test_build_transcript_window_interleaves_text_and_tool_in_order() -> None:
    """Window output must preserve original ordering across text and tool entries."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="First user line")]),
        ModelResponse(parts=[TextPart(content="First assistant line")], model_name="test-model"),
        ModelResponse(
            parts=[ToolCallPart(tool_name="search", args='{"q":"term"}', tool_call_id="call-1")],
            model_name="test-model",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search",
                    content="short result.",
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelResponse(
            parts=[TextPart(content="Follow-up assistant line")], model_name="test-model"
        ),
    ]

    window = build_transcript_window(messages)

    lines = window.splitlines()
    assert lines[0].startswith("User:")
    assert lines[1].startswith("Co:")
    assert lines[2].startswith("Tool(search):")
    assert lines[3].startswith("Tool result (search):")
    assert lines[4].startswith("Co:")


def test_tool_return_truncated_at_300() -> None:
    """ToolReturnPart with 400-char content must be truncated to 300 chars in the window."""
    long_content = "x" * 400
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_read",
                    content=long_content,
                    tool_call_id="call-2",
                ),
            ]
        ),
    ]
    window = build_transcript_window(messages)
    assert "Tool result (file_read):" in window
    result_line = next(line for line in window.splitlines() if "Tool result (file_read)" in line)
    prefix = "Tool result (file_read): "
    content_in_line = result_line[len(prefix) :]
    assert len(content_in_line) == 300


def test_large_read_tool_output_skipped() -> None:
    """ToolReturnPart matching Read-tool line-number prefix must be skipped."""
    read_tool_content = "1→ line content here"
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_read",
                    content=read_tool_content,
                    tool_call_id="call-3",
                ),
            ]
        ),
    ]
    window = build_transcript_window(messages)
    assert "Tool result (file_read)" not in window

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
    window_no_boundary = build_transcript_window(messages_no_boundary)
    assert "Tool result (list_dir)" not in window_no_boundary


def test_build_transcript_window_applies_independent_caps() -> None:
    """Text and tool caps apply independently before results are merged back in order."""
    messages: list = []
    for idx in range(60):
        messages.append(ModelRequest(parts=[UserPromptPart(content=f"user-line-{idx}")]))
    for idx in range(60):
        messages.append(
            ModelResponse(
                parts=[ToolCallPart(tool_name=f"tool-{idx}", args='{"arg":"value"}')],
                model_name="test-model",
            )
        )

    window = build_transcript_window(messages, max_text=50, max_tool=50)
    lines = window.splitlines()

    assert len(lines) == 100
    assert sum(1 for line in lines if line.startswith("User:")) == 50
    assert sum(1 for line in lines if line.startswith("Tool(")) == 50


def test_build_transcript_window_empty_messages_returns_empty_string() -> None:
    assert build_transcript_window([]) == ""
