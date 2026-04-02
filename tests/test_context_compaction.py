"""Tests for real token counts in compaction processors (TASK-1)."""

import asyncio

import pytest

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage, RunUsage

from co_cli.agent import build_agent
from co_cli.config import settings
from co_cli.context._history import (
    _latest_response_input_tokens,
    truncate_history_window,
)
from co_cli.deps import CoDeps, CoConfig
from co_cli.tools._shell_backend import ShellBackend

_CONFIG = CoConfig.from_settings(settings, cwd=__import__("pathlib").Path.cwd())
_AGENT = build_agent(config=_CONFIG).agent


def _make_ctx(config: CoConfig) -> RunContext:
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str, input_tokens: int = 0) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content=text)],
        usage=RequestUsage(input_tokens=input_tokens),
    )


def _make_messages(n: int, last_input_tokens: int = 0) -> list:
    """Alternating user/assistant messages; last assistant has specified token usage."""
    msgs = []
    for i in range(n // 2):
        msgs.append(_user(f"user turn {i}"))
        tokens = last_input_tokens if i == (n // 2 - 1) else 0
        msgs.append(_assistant(f"assistant turn {i}", input_tokens=tokens))
    if n % 2:
        msgs.append(_user(f"user turn {n // 2}"))
    return msgs


# ---------------------------------------------------------------------------
# Case 1: Cloud provider real usage triggers compaction (> 85% of default budget)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_triggers_on_real_input_tokens():
    """ModelResponse with input_tokens=90_000 triggers compaction (> 85% of 100k budget).

    llm_provider must be non-Ollama to use _DEFAULT_TOKEN_BUDGET (100k).
    With Ollama, the budget would be llm_num_ctx which may be much larger.
    """
    # 90_000 > int(100_000 * 0.85) = 85_000 → must compact
    msgs = _make_messages(10, last_input_tokens=90_000)
    # Use a non-Ollama provider so budget = _DEFAULT_TOKEN_BUDGET (100k), not llm_num_ctx
    config = CoConfig(max_history_messages=0, llm_provider="anthropic")
    ctx = _make_ctx(config)
    result = await truncate_history_window(ctx, msgs)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# Case 2: Char-estimate fallback when no usage data is available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_fallback_when_no_usage_data():
    """When no ModelResponse has usage data, _latest_response_input_tokens returns 0
    and compaction still triggers correctly via the count threshold."""
    # Verify the helper returns 0 with no usage data
    msgs_no_usage = _make_messages(10, last_input_tokens=0)
    assert _latest_response_input_tokens(msgs_no_usage) == 0

    # Functional: count trigger fires when usage is absent — fallback path doesn't break compaction
    config = CoConfig(max_history_messages=4)
    ctx = _make_ctx(config)
    result = await truncate_history_window(ctx, msgs_no_usage)
    assert len(result) < len(msgs_no_usage)


# ---------------------------------------------------------------------------
# Case 3: Ollama budget branch — compaction uses llm_num_ctx, not default budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_triggers_on_ollama_budget():
    """Ollama: input_tokens=7_200 with llm_num_ctx=8192 triggers compaction (> 85% of 8192=6963).

    7_200 > int(8192 * 0.85) = 6963, but 7_200 < int(100_000 * 0.85) = 85_000.
    Compaction must trigger against llm_num_ctx, not _DEFAULT_TOKEN_BUDGET.
    """
    msgs = _make_messages(10, last_input_tokens=7_200)
    config = CoConfig(
        max_history_messages=0,  # count trigger disabled
        llm_provider="ollama-openai",
        llm_num_ctx=8192,
    )
    assert config.uses_ollama_openai()
    ctx = _make_ctx(config)
    result = await truncate_history_window(ctx, msgs)
    assert len(result) < len(msgs)
