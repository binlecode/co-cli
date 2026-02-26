"""Functional tests for the deterministic components of _signal_analyzer.

Covers _keyword_precheck (phrase detection) and _build_window (turn extraction).
Both are zero-LLM-cost pre-filters: a broken precheck silently suppresses all
signal detection; a broken window builder starves the mini-agent of context.

Also covers analyze_for_signals E2E via the configured ollama model.
"""

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart

from co_cli._signal_analyzer import _build_window, _keyword_precheck, analyze_for_signals
from co_cli.agent import get_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


# ---------------------------------------------------------------------------
# _keyword_precheck — phrase category coverage
# ---------------------------------------------------------------------------


def test_precheck_correction_dont():
    assert _keyword_precheck([_user("don't use trailing comments")]) is True


def test_precheck_correction_stop_doing():
    assert _keyword_precheck([_user("please stop doing that")]) is True


def test_precheck_correction_never():
    assert _keyword_precheck([_user("never add docstrings everywhere")]) is True


def test_precheck_correction_avoid():
    assert _keyword_precheck([_user("avoid global state here")]) is True


def test_precheck_frustrated_why_did_you():
    assert _keyword_precheck([_user("why did you add that extra file?")]) is True


def test_precheck_frustrated_that_was_wrong():
    assert _keyword_precheck([_user("that was wrong, I wanted the original")]) is True


def test_precheck_preference_i_prefer():
    assert _keyword_precheck([_user("I prefer shorter responses")]) is True


def test_precheck_preference_always_use():
    assert _keyword_precheck([_user("always use 4-space indentation")]) is True


def test_precheck_preference_use_instead():
    assert _keyword_precheck([_user("use instead of trailing comments")]) is True


def test_precheck_frustrated_not_what_i():
    assert _keyword_precheck([_user("that's not what i asked for")]) is True


def test_precheck_case_insensitive():
    assert _keyword_precheck([_user("DON'T do that again")]) is True


# ---------------------------------------------------------------------------
# _keyword_precheck — negative cases
# ---------------------------------------------------------------------------


def test_precheck_neutral_question():
    assert _keyword_precheck([_user("what time is it in Tokyo?")]) is False


def test_precheck_only_assistant_messages():
    assert _keyword_precheck([_assistant("Here is the result.")]) is False


# ---------------------------------------------------------------------------
# _keyword_precheck — last-message-only semantics
# ---------------------------------------------------------------------------


def test_precheck_fires_on_latest_user_message():
    """Signal in most recent user message triggers precheck."""
    messages = [
        _user("what time is it?"),
        _assistant("It's 3pm."),
        _user("don't use trailing comments"),
    ]
    assert _keyword_precheck(messages) is True


def test_precheck_ignores_signal_in_earlier_message():
    """Signal only in an older message does NOT trigger precheck."""
    messages = [
        _user("don't use trailing comments"),
        _assistant("Got it, I'll avoid that."),
        _user("what time is it?"),
    ]
    assert _keyword_precheck(messages) is False


# ---------------------------------------------------------------------------
# _build_window — turn extraction
# ---------------------------------------------------------------------------


def test_window_single_user_message():
    window = _build_window([_user("hello there")])
    assert "User: hello there" in window


def test_window_includes_assistant_turns():
    messages = [_user("hello"), _assistant("Hi! How can I help?")]
    window = _build_window(messages)
    assert "User: hello" in window
    assert "Co: Hi! How can I help?" in window


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
async def test_analyze_no_signal():
    """Neutral question produces no signal."""
    agent, _, _ = get_agent()
    messages = [_user("what time is it in Tokyo?")]
    result = await analyze_for_signals(messages, agent.model)
    assert result.found is False
