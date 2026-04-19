"""Regression tests for interrupt handling in the foreground turn loop."""

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart

from co_cli.context.orchestrate import _build_interrupted_turn_result, _TurnState


def test_build_interrupted_turn_result_drops_dangling_tool_call():
    """Interrupts must discard unanswered tool calls before appending the abort marker."""
    clean_request = ModelRequest(parts=[UserPromptPart(content="run ls")])
    dangling_response = ModelResponse(
        parts=[ToolCallPart(tool_name="shell", args='{"cmd": "ls"}', tool_call_id="call-x")]
    )
    turn_state = _TurnState(
        current_input=None,
        current_history=[clean_request, dangling_response],
    )

    result = _build_interrupted_turn_result(turn_state)

    assert result.interrupted is True
    assert result.outcome == "continue"
    assert dangling_response not in result.messages
    last = result.messages[-1]
    assert isinstance(last, ModelRequest)
    assert any(
        "interrupted" in part.content.lower()
        for part in last.parts
        if isinstance(part, UserPromptPart)
    )


def test_build_interrupted_turn_result_keeps_clean_history():
    """Interrupts must preserve history when the last response has no dangling tool call."""
    clean_request = ModelRequest(parts=[UserPromptPart(content="hello")])
    clean_response = ModelResponse(parts=[])
    turn_state = _TurnState(
        current_input=None,
        current_history=[clean_request, clean_response],
    )

    result = _build_interrupted_turn_result(turn_state)

    assert result.interrupted is True
    assert clean_request in result.messages
    assert clean_response in result.messages
