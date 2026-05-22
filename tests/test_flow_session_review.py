"""Behavioral tests for session review support utilities."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage


def test_serialize_messages_include_tool_results_false() -> None:
    """serialize_messages with include_tool_results=False drops ToolReturnPart."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from co_cli.context.summarization import serialize_messages

    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="skill_view", args='{"name":"foo"}', tool_call_id="1"),
            ],
            model_name="test",
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="skill_view", content="result", tool_call_id="1")]
        ),
        ModelResponse(parts=[TextPart(content="found it")], model_name="test"),
    ]

    with_results = serialize_messages(messages, [], include_tool_results=True)
    without_results = serialize_messages(messages, [], include_tool_results=False)

    assert "tool_result" in with_results
    assert "tool_result" not in without_results
    assert "tool_call" in without_results
    assert "found it" in without_results
