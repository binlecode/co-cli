"""Unit tests for the clarify tool."""

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

from co_cli.agent.core import build_agent
from co_cli.config.core import settings
from co_cli.deps import CoDeps
from co_cli.tools.approvals import QuestionRequired
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.user_input import clarify

_AGENT = build_agent(config=settings)


def _make_ctx(*, tool_call_approved: bool = False) -> RunContext:
    deps = CoDeps(shell=ShellBackend(), config=settings)
    return RunContext(
        deps=deps,
        model=_AGENT.model,
        usage=RunUsage(),
        tool_name="clarify",
        tool_call_approved=tool_call_approved,
    )


@pytest.mark.asyncio
async def test_request_user_input_raises_question_required_when_not_approved() -> None:
    """Tool raises QuestionRequired when user_answer is absent on first (unapproved) call."""
    ctx = _make_ctx(tool_call_approved=False)
    with pytest.raises(QuestionRequired) as exc_info:
        await clarify(ctx, question="What is your name?")
    assert exc_info.value.question == "What is your name?"
    assert exc_info.value.options is None


@pytest.mark.asyncio
async def test_request_user_input_raises_question_required_with_options() -> None:
    """Tool raises QuestionRequired carrying the options list."""
    ctx = _make_ctx(tool_call_approved=False)
    with pytest.raises(QuestionRequired) as exc_info:
        await clarify(ctx, question="Continue?", options=["yes", "no"])
    assert exc_info.value.options == ["yes", "no"]


@pytest.mark.asyncio
async def test_request_user_input_llm_escape_hatch_still_raises() -> None:
    """Tool raises QuestionRequired even when model pre-supplies user_answer on first call."""
    ctx = _make_ctx(tool_call_approved=False)
    with pytest.raises(QuestionRequired):
        await clarify(ctx, question="Confirm?", user_answer="yes")


@pytest.mark.asyncio
async def test_request_user_input_returns_answer_when_approved() -> None:
    """Tool returns the injected user_answer on the resumed (approved) call."""
    ctx = _make_ctx(tool_call_approved=True)
    result = await clarify(ctx, question="What is your name?", user_answer="Alice")
    assert result.return_value == "Alice"


@pytest.mark.asyncio
async def test_request_user_input_returns_answer_with_valid_option() -> None:
    """Tool returns user_answer when it matches one of the options."""
    ctx = _make_ctx(tool_call_approved=True)
    result = await clarify(ctx, question="Continue?", options=["yes", "no"], user_answer="yes")
    assert result.return_value == "yes"


@pytest.mark.asyncio
async def test_request_user_input_returns_error_for_invalid_option() -> None:
    """Tool returns an error when user_answer is not among the options."""
    ctx = _make_ctx(tool_call_approved=True)
    result = await clarify(ctx, question="Continue?", options=["yes", "no"], user_answer="maybe")
    assert result.metadata is not None
    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_request_user_input_returns_error_when_no_answer_on_resume() -> None:
    """Tool returns an error when resumed but user_answer is still None."""
    ctx = _make_ctx(tool_call_approved=True)
    result = await clarify(ctx, question="What?")
    assert result.metadata is not None
    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_question_required_metadata_has_question_discriminator() -> None:
    """QuestionRequired populates metadata with 'question' key for orchestrator discrimination."""
    ctx = _make_ctx(tool_call_approved=False)
    with pytest.raises(QuestionRequired) as exc_info:
        await clarify(ctx, question="Pick one.", options=["a", "b"])
    exc = exc_info.value
    assert exc.metadata == {"question": "Pick one.", "options": ["a", "b"]}
    assert "question" in exc.metadata
    assert "_kind" not in exc.metadata
