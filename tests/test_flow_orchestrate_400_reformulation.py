"""Tests for HTTP-400 reformulation helper in run_turn().

Production path: co_cli/context/orchestrate.py:_apply_400_reformulation()
Called from run_turn() when ModelHTTPError.status_code == 400 and budget > 0.
"""

from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest, UserPromptPart

from co_cli.context.orchestrate import _apply_400_reformulation, _TurnState


def _fake_400(body: str = "invalid tool call JSON") -> object:
    """Duck-type ModelHTTPError for the two attributes the helper reads (status_code, body)."""
    return SimpleNamespace(status_code=400, body=body)


def _make_state(
    budget: int = 2,
    current_input: str | None = "original",
) -> _TurnState:
    return _TurnState(current_input=current_input, current_history=[], tool_reformat_budget=budget)


# ---------------------------------------------------------------------------
# budget decrement
# ---------------------------------------------------------------------------


def test_budget_decrements_on_each_call() -> None:
    """Budget decrements from 2 → 1 → 0 across two calls."""
    state = _make_state(budget=2)
    assert _apply_400_reformulation(state, _fake_400()) is True
    assert state.tool_reformat_budget == 1
    assert _apply_400_reformulation(state, _fake_400()) is True
    assert state.tool_reformat_budget == 0


def test_budget_exhausted_returns_false() -> None:
    """When budget is already 0, helper returns False without mutating state."""
    state = _make_state(budget=0)
    history_before = state.current_history
    result = _apply_400_reformulation(state, _fake_400())
    assert result is False
    assert state.tool_reformat_budget == 0
    assert state.current_history is history_before


# ---------------------------------------------------------------------------
# reflection content shape
# ---------------------------------------------------------------------------


def test_appended_message_is_model_request_with_user_prompt_part() -> None:
    """Appended entry is a ModelRequest containing exactly one UserPromptPart."""
    state = _make_state(budget=1)
    _apply_400_reformulation(state, _fake_400("bad json"))
    assert len(state.current_history) == 1
    msg = state.current_history[0]
    assert isinstance(msg, ModelRequest)
    assert len(msg.parts) == 1
    assert isinstance(msg.parts[0], UserPromptPart)


def test_reflection_content_mentions_reformulate_and_error_body() -> None:
    """Reflection content includes the word 'reformulate' and the error body string."""
    body = "unexpected_field_xyz"
    state = _make_state(budget=2)
    _apply_400_reformulation(state, _fake_400(body))
    content = state.current_history[0].parts[0].content
    assert "reformulate" in content
    assert body in content


# ---------------------------------------------------------------------------
# history append (not replace)
# ---------------------------------------------------------------------------


def test_history_grows_by_one_and_preserves_originals() -> None:
    """Each call appends exactly one ModelRequest; prior messages are preserved."""
    prior = ModelRequest(parts=[UserPromptPart(content="prior message")])
    state = _TurnState(current_input="q", current_history=[prior], tool_reformat_budget=2)
    _apply_400_reformulation(state, _fake_400())
    assert len(state.current_history) == 2
    assert state.current_history[0] is prior


def test_two_calls_append_two_messages() -> None:
    """Two reformulation calls produce two new entries in history."""
    state = _make_state(budget=2)
    _apply_400_reformulation(state, _fake_400("first error"))
    _apply_400_reformulation(state, _fake_400("second error"))
    assert len(state.current_history) == 2
    assert "first error" in state.current_history[0].parts[0].content
    assert "second error" in state.current_history[1].parts[0].content


# ---------------------------------------------------------------------------
# current_input cleared
# ---------------------------------------------------------------------------


def test_current_input_is_none_after_call() -> None:
    """After a successful reformulation call, turn_state.current_input is None."""
    state = _make_state(budget=1, current_input="user question")
    assert state.current_input == "user question"
    _apply_400_reformulation(state, _fake_400())
    assert state.current_input is None


def test_current_input_unchanged_when_budget_exhausted() -> None:
    """When budget is 0, current_input is not modified."""
    state = _make_state(budget=0, current_input="preserved")
    _apply_400_reformulation(state, _fake_400())
    assert state.current_input == "preserved"
