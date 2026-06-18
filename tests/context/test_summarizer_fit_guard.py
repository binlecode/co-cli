"""Pre-flight fit guard in ``summarize_messages``: an oversized assembled prompt
is detected locally and never reaches the provider, while an in-budget prompt
calls through to ``llm_call`` exactly once and returns its output.

Functional seam: ``co_cli.context.summarization.llm_call`` is monkeypatched to
record invocations and return a fixed string, so the assertions are observable
outcomes (was the provider call made? what came back?) with no real LLM traffic.
"""

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from tests._settings import SETTINGS_NO_MCP

from co_cli.context import summarization
from co_cli.context.summarization import SummarizerInputTooLargeError, summarize_messages
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_MESSAGES = [
    ModelRequest(parts=[UserPromptPart(content="Add password hashing to signup. Use Argon2id.")]),
    ModelResponse(parts=[TextPart(content="Done — signup now uses Argon2id hashing.")]),
]


def _make_deps(model_max_context_tokens: int) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=build_model(SETTINGS_NO_MCP.llm),
        config=SETTINGS_NO_MCP.model_copy(deep=True),
        session=CoSessionState(),
        model_max_context_tokens=model_max_context_tokens,
    )


@pytest.mark.asyncio
async def test_oversized_prompt_raises_without_calling_provider(monkeypatch):
    """A window smaller than the assembled prompt makes summarize_messages raise
    SummarizerInputTooLarge before the provider is touched."""
    calls = []

    async def _recording_llm_call(*args, **kwargs):
        calls.append((args, kwargs))
        return "should never be returned"

    monkeypatch.setattr(summarization, "llm_call", _recording_llm_call)
    deps = _make_deps(model_max_context_tokens=100)

    with pytest.raises(SummarizerInputTooLargeError):
        await summarize_messages(deps, _MESSAGES)

    assert calls == [], "llm_call was reached for an oversized region"


@pytest.mark.asyncio
async def test_in_budget_prompt_calls_provider_once_and_returns_output(monkeypatch):
    """An ample window lets the call proceed: llm_call runs exactly once and its
    output is returned."""
    calls = []

    async def _recording_llm_call(*args, **kwargs):
        calls.append((args, kwargs))
        return "## Active Task\nthe recap"

    monkeypatch.setattr(summarization, "llm_call", _recording_llm_call)
    deps = _make_deps(model_max_context_tokens=200_000)

    result = await summarize_messages(deps, _MESSAGES)

    assert len(calls) == 1, "in-budget summary did not call the provider exactly once"
    assert result == "## Active Task\nthe recap"
