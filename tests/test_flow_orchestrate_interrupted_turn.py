"""Behavioral tests for _build_interrupted_turn_result truncation logic.

Production path: co_cli/context/orchestrate.py:_build_interrupted_turn_result
No LLM needed — pure function over _TurnState.
"""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)

from co_cli.context.orchestrate import _build_interrupted_turn_result, _TurnState


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp_text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def _resp_tool() -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name="shell", args="{}")])


def test_interrupted_result_drops_unanswered_tool_call_response() -> None:
    """History ending with a ToolCallPart ModelResponse has that response stripped.

    Failure mode: unanswered ToolCallPart stays in history → next-turn model
    sees a dangling call without a return → pydantic-ai raises UnexpectedModelBehavior.
    """
    history = [
        _req("first user turn"),
        _resp_text("first model text"),
        _req("second user turn"),
        _resp_tool(),  # unanswered tool call — no ToolReturnPart follows
    ]
    turn_state = _TurnState(current_input="second user turn", current_history=history)

    result = _build_interrupted_turn_result(turn_state)

    assert result.interrupted is True
    # The ToolCallPart ModelResponse must be absent
    for msg in result.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                assert not isinstance(part, ToolCallPart), (
                    "Unanswered ToolCallPart ModelResponse must be dropped on interrupt"
                )
    # Last message is the abort marker (a ModelRequest with interrupt text)
    last = result.messages[-1]
    assert isinstance(last, ModelRequest)
    user_contents = [part.content for part in last.parts if isinstance(part, UserPromptPart)]
    assert any("interrupted" in c for c in user_contents), (
        "Abort marker must contain 'interrupted' in its UserPromptPart content"
    )


def test_interrupted_result_preserves_clean_history_and_appends_abort_marker() -> None:
    """Clean history (no dangling ToolCallPart) is preserved with abort marker appended.

    Failure mode: clean history silently truncated → conversation context lost
    on interrupt; the model restarts without prior exchange.
    """
    history = [
        _req("first user turn"),
        _resp_text("first model text"),
        _req("second user turn"),
        _resp_text("second model text"),  # clean end — no tool calls
    ]
    turn_state = _TurnState(current_input="second user turn", current_history=history)

    result = _build_interrupted_turn_result(turn_state)

    assert result.interrupted is True
    # All original messages must be present (order preserved)
    assert result.messages[:4] == history, (
        "Clean history must be fully preserved before the abort marker"
    )
    # Abort marker is the final element
    last = result.messages[-1]
    assert isinstance(last, ModelRequest)
    assert len(result.messages) == len(history) + 1
    user_contents = [part.content for part in last.parts if isinstance(part, UserPromptPart)]
    assert any("interrupted" in c for c in user_contents)
