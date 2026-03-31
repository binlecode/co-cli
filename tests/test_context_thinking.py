"""Tests for ThinkingPart handling in _find_first_run_end (TASK-2)."""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)

from co_cli.context._history import _find_first_run_end


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


# ---------------------------------------------------------------------------
# Case 1: ThinkingPart-only response is accepted as the first-run anchor
# ---------------------------------------------------------------------------


def test_find_first_run_end_accepts_thinking_only_response():
    """ModelResponse with only ThinkingPart is a valid first-run anchor.

    Without this fix, _find_first_run_end would return 0 (only the initial
    ModelRequest pinned), losing the thinking turn from the head.
    """
    messages = [
        _user("hello"),
        ModelResponse(parts=[ThinkingPart(content="let me think...")]),
        _user("follow up"),
        ModelResponse(parts=[TextPart(content="answer")]),
    ]
    idx = _find_first_run_end(messages)
    # Index 1 is the ThinkingPart-only response — must be anchored
    assert idx == 1


# ---------------------------------------------------------------------------
# Case 2: Response with both ThinkingPart and TextPart is correctly anchored
# ---------------------------------------------------------------------------


def test_find_first_run_end_accepts_thinking_and_text_response():
    """ModelResponse with both ThinkingPart and TextPart is correctly anchored."""
    messages = [
        _user("hello"),
        ModelResponse(parts=[ThinkingPart(content="thinking..."), TextPart(content="answer")]),
        _user("follow up"),
        ModelResponse(parts=[TextPart(content="more")]),
    ]
    idx = _find_first_run_end(messages)
    assert idx == 1
