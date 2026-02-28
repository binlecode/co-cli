"""Functional tests for _signal_analyzer.

Covers _build_window (turn extraction) and analyze_for_signals E2E via the
configured LLM model. Signal detection is fully LLM-driven — no heuristic
precheck to test separately.
"""

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart, TextPart

from co_cli._signal_analyzer import _build_window, analyze_for_signals
from co_cli.agent import get_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


# ---------------------------------------------------------------------------
# _build_window — turn extraction
# ---------------------------------------------------------------------------


def test_window_capped_at_10_lines():
    """Window is capped at the last 10 lines (~5 turns)."""
    messages = []
    for i in range(10):
        messages.append(_user(f"user message {i}"))
        messages.append(_assistant(f"response {i}"))
    lines = [ln for ln in _build_window(messages).splitlines() if ln.strip()]
    assert len(lines) <= 10


def test_window_preserves_most_recent_turns():
    """Capped window keeps the most recent messages, not the oldest."""
    messages = []
    for i in range(10):
        messages.append(_user(f"old message {i}"))
        messages.append(_assistant(f"old response {i}"))
    messages.append(_user("most recent message"))
    assert "most recent message" in _build_window(messages)


# ---------------------------------------------------------------------------
# _build_window edge cases — probing for silent data loss
# ---------------------------------------------------------------------------


def test_window_excludes_tool_return_content():
    """ToolReturnPart content in ModelRequest is not emitted as a window line.

    _build_window only extracts UserPromptPart and TextPart. A ModelRequest
    that contains a ToolReturnPart (not UserPromptPart) must not add any line,
    otherwise the mini-agent receives raw tool output as if it were a user turn.
    """
    messages = [
        _user("search for cats"),
        ModelResponse(parts=[ToolCallPart(tool_name="web_search", args="{}", tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="cat results here", tool_call_id="c1")]),
    ]
    window = _build_window(messages)
    assert "cat results here" not in window, (
        "Tool return content leaked into the window. "
        "_build_window should only emit UserPromptPart lines, not ToolReturnPart."
    )
    assert "User: search for cats" in window


def test_window_excludes_tool_call_only_model_response():
    """ModelResponse containing only ToolCallPart adds no 'Co:' line.

    If a response has no TextPart, it should contribute nothing to the window.
    Leaking a ToolCallPart repr into the window would confuse the signal agent.
    """
    messages = [
        _user("search"),
        ModelResponse(parts=[ToolCallPart(tool_name="web_search", args="{}", tool_call_id="c1")]),
        _user("what did you find?"),
    ]
    window = _build_window(messages)
    lines = [ln for ln in window.splitlines() if ln.strip()]
    non_user_lines = [ln for ln in lines if not ln.startswith("User:")]
    assert not non_user_lines, (
        f"Expected no 'Co:' lines when ModelResponse has no TextPart. "
        f"Got: {non_user_lines}"
    )


def test_window_with_only_tool_messages_returns_empty():
    """History containing only tool calls and returns produces empty window.

    When the model has not yet produced any text and no user has spoken
    (e.g., mid-tool-chain), the window should be empty — not garbage.
    """
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name="recall_memory", args="{}", tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="recall_memory", content="memory content", tool_call_id="c1")]),
    ]
    window = _build_window(messages)
    assert window.strip() == "", (
        f"Window should be empty when there are no user or assistant text parts. "
        f"Got: {window!r}"
    )


# ---------------------------------------------------------------------------
# analyze_for_signals — LLM E2E (ollama, configured via ~/.config/co-cli/settings.json)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_correction_high_confidence():
    """Clear correction message classifies as correction with high confidence."""
    agent, _, _ = get_agent()
    messages = [_user("don't use trailing comments in the code")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is True
    assert result.tag == "correction"
    assert result.confidence == "high"


@pytest.mark.asyncio
async def test_analyze_preference_detected():
    """Stated preference message is detected as a signal."""
    agent, _, _ = get_agent()
    messages = [_user("I prefer shorter responses")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is True
    assert result.tag == "preference"


@pytest.mark.asyncio
async def test_analyze_decision_high_confidence():
    """Team decision statement is detected as high-confidence preference."""
    agent, _, _ = get_agent()
    messages = [_user("we decided to use PostgreSQL from now on")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is True
    assert result.confidence == "high"


@pytest.mark.asyncio
async def test_analyze_migration_high_confidence():
    """Migration statement is detected as high-confidence preference."""
    agent, _, _ = get_agent()
    messages = [_user("we switched from REST to GraphQL last month")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is True
    assert result.confidence == "high"


@pytest.mark.asyncio
async def test_analyze_habit_detected():
    """Habit disclosure is detected as a signal."""
    agent, _, _ = get_agent()
    messages = [_user("I've been putting everything in one big file so far")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is True


@pytest.mark.asyncio
async def test_analyze_no_signal():
    """Neutral question produces no signal."""
    agent, _, _ = get_agent()
    messages = [_user("what time is it in Tokyo?")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is False
