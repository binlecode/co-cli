"""Tests for _format_conversation and _render_tool_return in co_cli.memory._summary."""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.memory._summary import _format_conversation, _render_tool_return


def test_user_prompt_part_renders_with_user_prefix() -> None:
    """UserPromptPart renders as [USER]: content."""
    messages = [ModelRequest(parts=[UserPromptPart(content="hello from user")])]
    result = _format_conversation(messages)
    assert result == "[USER]: hello from user"


def test_text_part_renders_with_assistant_prefix() -> None:
    """TextPart in ModelResponse renders as [ASSISTANT]: content."""
    messages = [ModelResponse(parts=[TextPart(content="hello from assistant")], model_name="m")]
    result = _format_conversation(messages)
    assert result == "[ASSISTANT]: hello from assistant"


def test_tool_call_part_renders_tool_name_only() -> None:
    """ToolCallPart renders as [ASSISTANT][Called: tool_name] with no args inlined."""
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="file_read",
                    args='{"path": "/tmp/x"}',
                    tool_call_id="c1",
                )
            ],
            model_name="m",
        )
    ]
    result = _format_conversation(messages)
    assert result == "[ASSISTANT][Called: file_read]"
    assert '{"path": "/tmp/x"}' not in result


def test_tool_return_short_content_untruncated() -> None:
    """ToolReturnPart with content <= 500 chars renders untruncated as [TOOL:name]: content."""
    content = "x" * 500
    part = ToolReturnPart(tool_name="file_read", content=content, tool_call_id="c1")
    result = _render_tool_return(part)
    assert result == f"[TOOL:file_read]: {content}"
    assert "truncated" not in result


def test_tool_return_long_content_is_truncated() -> None:
    """ToolReturnPart with content > 500 chars is head-250 + marker + tail-250 truncated."""
    head = "H" * 250
    middle = "M" * 200
    tail = "T" * 250
    content = head + middle + tail
    assert len(content) == 700

    part = ToolReturnPart(tool_name="shell", content=content, tool_call_id="c2")
    result = _render_tool_return(part)

    assert result.startswith("[TOOL:shell]: ")
    body = result[len("[TOOL:shell]: ") :]
    assert body.startswith(head)
    assert body.endswith(tail)
    assert "\n...[truncated]...\n" in body
    # Total body length = 250 + len("\n...[truncated]...\n") + 250
    assert len(body) == 250 + len("\n...[truncated]...\n") + 250


def test_system_prompt_part_is_skipped() -> None:
    """SystemPromptPart in a ModelRequest is silently skipped."""
    messages = [
        ModelRequest(parts=[SystemPromptPart(content="you are helpful")]),
        ModelRequest(parts=[UserPromptPart(content="hi")]),
    ]
    result = _format_conversation(messages)
    assert "you are helpful" not in result
    assert result == "[USER]: hi"


def test_thinking_part_is_skipped() -> None:
    """ThinkingPart in a ModelResponse is silently skipped — only TextPart renders."""
    try:
        from pydantic_ai.messages import ThinkingPart
    except ImportError:
        # ThinkingPart not available in this pydantic-ai version — skip gracefully
        return

    messages = [
        ModelResponse(
            parts=[ThinkingPart(content="hmm, let me think"), TextPart(content="the answer")],
            model_name="m",
        )
    ]
    result = _format_conversation(messages)
    assert "hmm, let me think" not in result
    assert result == "[ASSISTANT]: the answer"


def test_multi_message_conversation_formats_all_parts() -> None:
    """Multi-message conversation renders all relevant parts in order."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="what is 2+2?")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="calculator", args='{"expr": "2+2"}', tool_call_id="c3"),
            ],
            model_name="m",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="calculator", content="4", tool_call_id="c3"),
            ]
        ),
        ModelResponse(parts=[TextPart(content="The answer is 4.")], model_name="m"),
    ]
    result = _format_conversation(messages)
    parts = result.split("\n\n")
    assert parts[0] == "[USER]: what is 2+2?"
    assert parts[1] == "[ASSISTANT][Called: calculator]"
    assert parts[2] == "[TOOL:calculator]: 4"
    assert parts[3] == "[ASSISTANT]: The answer is 4."
