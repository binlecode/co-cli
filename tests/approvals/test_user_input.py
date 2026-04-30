"""Unit tests for the clarify tool."""

import json

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


_Q_FREE = {"question": "What is your name?"}
_Q_RADIO = {
    "question": "Continue?",
    "options": [{"label": "yes", "description": ""}, {"label": "no", "description": ""}],
}
_Q_MULTI = {
    "question": "Pick features",
    "options": [{"label": "a", "description": ""}, {"label": "b", "description": ""}],
    "multiple": True,
}


@pytest.mark.asyncio
async def test_clarify_raises_question_required_when_not_approved() -> None:
    """clarify raises QuestionRequired carrying the questions list when unapproved."""
    ctx = _make_ctx(tool_call_approved=False)
    with pytest.raises(QuestionRequired) as exc_info:
        await clarify(ctx, questions=[_Q_FREE])
    assert exc_info.value.questions == [_Q_FREE]


@pytest.mark.asyncio
async def test_clarify_raises_with_questions_metadata() -> None:
    """QuestionRequired metadata has 'questions' discriminator key."""
    ctx = _make_ctx(tool_call_approved=False)
    with pytest.raises(QuestionRequired) as exc_info:
        await clarify(ctx, questions=[_Q_FREE, _Q_RADIO])
    exc = exc_info.value
    assert "questions" in exc.metadata
    assert exc.metadata["questions"] == [_Q_FREE, _Q_RADIO]


@pytest.mark.asyncio
async def test_clarify_llm_escape_hatch_still_raises() -> None:
    """clarify raises QuestionRequired even when model pre-supplies user_answers."""
    ctx = _make_ctx(tool_call_approved=False)
    with pytest.raises(QuestionRequired):
        await clarify(ctx, questions=[_Q_FREE], user_answers=["Alice"])


@pytest.mark.asyncio
async def test_clarify_single_question_returns_answer_when_approved() -> None:
    """Approved call with one question returns JSON-encoded single-element list."""
    ctx = _make_ctx(tool_call_approved=True)
    result = await clarify(ctx, questions=[_Q_FREE], user_answers=["Alice"])
    assert result.return_value == json.dumps(["Alice"])


@pytest.mark.asyncio
async def test_clarify_batch_returns_all_answers_positionally_aligned() -> None:
    """Batch clarify returns JSON list positionally aligned to the questions list."""
    ctx = _make_ctx(tool_call_approved=True)
    questions = [_Q_FREE, _Q_RADIO, _Q_MULTI]
    answers = ["Alice", "yes", "a,b"]
    result = await clarify(ctx, questions=questions, user_answers=answers)
    assert result.return_value == json.dumps(answers)


@pytest.mark.asyncio
async def test_clarify_returns_error_when_no_answers_on_resume() -> None:
    """Approved resumed call with no answers returns error."""
    ctx = _make_ctx(tool_call_approved=True)
    result = await clarify(ctx, questions=[_Q_FREE])
    assert result.metadata is not None
    assert result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_clarify_returns_error_for_mismatched_answer_count() -> None:
    """Approved call where len(user_answers) != len(questions) returns error."""
    ctx = _make_ctx(tool_call_approved=True)
    result = await clarify(ctx, questions=[_Q_FREE, _Q_RADIO], user_answers=["only one"])
    assert result.metadata is not None
    assert result.metadata.get("error") is True
