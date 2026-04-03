"""Tests for compaction budget resolution and token-triggered compaction."""

import pytest

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage, RunUsage

from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.agent import build_agent
from co_cli.config import ROLE_REASONING, settings
from co_cli.context._compaction import (
    _DEFAULT_TOKEN_BUDGET,
    latest_response_input_tokens,
    resolve_compaction_budget,
)
from co_cli.context._history import truncate_history_window
from co_cli.deps import CoDeps, CoConfig
from co_cli.tools._shell_backend import ShellBackend

_CONFIG = CoConfig.from_settings(settings, cwd=__import__("pathlib").Path.cwd())
_AGENT = build_agent(config=_CONFIG)


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
    config = CoConfig(llm_provider="anthropic")
    ctx = _make_ctx(config)
    result = await truncate_history_window(ctx, msgs)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# Case 2: Char-estimate fallback when no usage data is available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_fallback_when_no_usage_data():
    """When no ModelResponse has usage data, latest_response_input_tokens returns 0
    and compaction still triggers correctly via the char-estimate fallback.

    Uses a tiny Ollama budget (llm_num_ctx=30) so the char-estimate
    (~33 tokens from 10 messages) exceeds int(30 * 0.85) = 25.
    """
    msgs_no_usage = _make_messages(10, last_input_tokens=0)
    assert latest_response_input_tokens(msgs_no_usage) == 0

    # Char-estimate fallback: ~135 chars / 4 ≈ 33 tokens > threshold 25
    config = CoConfig(llm_provider="ollama-openai", llm_num_ctx=30)
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
        llm_provider="ollama-openai",
        llm_num_ctx=8192,
    )
    assert config.uses_ollama_openai()
    ctx = _make_ctx(config)
    result = await truncate_history_window(ctx, msgs)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# resolve_compaction_budget() — pure function, no LLM calls
# ---------------------------------------------------------------------------


def _make_registry_with_context_window(
    context_window: int | None,
    max_tokens: int | None = None,
) -> ModelRegistry:
    """Build a minimal ModelRegistry with a reasoning role that has the given context_window."""
    registry = ModelRegistry()
    settings = ModelSettings(max_tokens=max_tokens) if max_tokens else None
    resolved = ResolvedModel(model=None, settings=settings, context_window=context_window)
    registry._models[ROLE_REASONING] = resolved
    return registry


def test_budget_gemini_model_spec():
    """Gemini model with context_window=1M, max_tokens=65536 → budget = 1M - 65536."""
    registry = _make_registry_with_context_window(1_048_576, max_tokens=65_536)
    config = CoConfig(llm_provider="gemini")
    budget = resolve_compaction_budget(config, registry)
    assert budget == 1_048_576 - 65_536


def test_budget_ollama_llm_num_ctx_overrides_spec():
    """Ollama: llm_num_ctx overrides context_window from spec (Modelfile is truth)."""
    registry = _make_registry_with_context_window(262_144, max_tokens=32_768)
    config = CoConfig(llm_provider="ollama-openai", llm_num_ctx=32_768)
    budget = resolve_compaction_budget(config, registry)
    # llm_num_ctx (32768) overrides spec (262144), so effective ctx_window = 32768
    # 32768 - max_tokens(32768) = 0, but floor = effective_ctx_window // 2 = 16384
    assert budget == 32_768 // 2


def test_budget_ollama_no_spec_falls_back_to_llm_num_ctx():
    """Ollama with no context_window in quirks → falls back to llm_num_ctx."""
    registry = _make_registry_with_context_window(None)
    config = CoConfig(llm_provider="ollama-openai", llm_num_ctx=32_768)
    budget = resolve_compaction_budget(config, registry)
    assert budget == 32_768


def test_budget_no_registry_returns_default():
    """No registry (sub-agent/test path) → _DEFAULT_TOKEN_BUDGET."""
    config = CoConfig(llm_provider="gemini")
    budget = resolve_compaction_budget(config, None)
    assert budget == _DEFAULT_TOKEN_BUDGET


def test_budget_floor_prevents_negative():
    """When max_tokens > context_window/2, floor kicks in at context_window//2."""
    registry = _make_registry_with_context_window(100_000, max_tokens=80_000)
    config = CoConfig(llm_provider="gemini")
    budget = resolve_compaction_budget(config, registry)
    # max(100K - 80K, 100K // 2) = max(20K, 50K) = 50K
    assert budget == 50_000
