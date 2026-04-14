"""Tests for compaction budget resolution and token-triggered compaction."""

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage, RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import Settings, settings
from co_cli.context._history import summarize_history_window
from co_cli.context.summarization import (
    _DEFAULT_TOKEN_BUDGET,
    _PERSONALITY_COMPACTION_ADDENDUM,
    _SUMMARIZE_PROMPT,
    _build_summarizer_prompt,
    latest_response_input_tokens,
    resolve_compaction_budget,
)
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend

_CONFIG = settings
_AGENT = build_agent(config=_CONFIG)


def _make_ctx(config: Settings) -> RunContext:
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
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    ctx = _make_ctx(config)
    result = await summarize_history_window(ctx, msgs)
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
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama-openai", "num_ctx": 30})
    )
    ctx = _make_ctx(config)
    result = await summarize_history_window(ctx, msgs_no_usage)
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
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama-openai", "num_ctx": 8192})
    )
    assert config.llm.uses_ollama_openai()
    ctx = _make_ctx(config)
    result = await summarize_history_window(ctx, msgs)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# resolve_compaction_budget() — pure function, no LLM calls
# ---------------------------------------------------------------------------


def test_budget_gemini_model_spec():
    """Gemini model with context_window=1M → budget = 1M - 16384 output reserve."""
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "gemini"}))
    budget = resolve_compaction_budget(config, 1_048_576)
    # max(1_048_576 - 16384, 1_048_576 // 2) = 1_032_192
    assert budget == 1_048_576 - 16_384


def test_budget_ollama_llm_num_ctx_overrides_spec():
    """Ollama: llm_num_ctx overrides context_window from spec (Modelfile is truth)."""
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama-openai", "num_ctx": 32_768})
    )
    budget = resolve_compaction_budget(config, 262_144)
    # llm_num_ctx (32768) overrides spec (262144), so effective ctx_window = 32768
    # max(32768 - 16384, 32768 // 2) = max(16384, 16384) = 16384
    assert budget == 32_768 // 2


def test_budget_ollama_no_spec_falls_back_to_llm_num_ctx():
    """Ollama with no resolved context_window → falls back to llm_num_ctx."""
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama-openai", "num_ctx": 32_768})
    )
    budget = resolve_compaction_budget(config, None)
    assert budget == 32_768


def test_budget_no_context_window_returns_default():
    """No context_window (sub-agent/test path) → _DEFAULT_TOKEN_BUDGET."""
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "gemini"}))
    budget = resolve_compaction_budget(config, None)
    assert budget == _DEFAULT_TOKEN_BUDGET


def test_budget_floor_prevents_negative():
    """Small context_window → floor at context_window//2."""
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "gemini"}))
    # context_window=20000: max(20000 - 16384, 10000) = max(3616, 10000) = 10000
    budget = resolve_compaction_budget(config, 20_000)
    assert budget == 10_000


# ---------------------------------------------------------------------------
# _build_summarizer_prompt() — pure function, 4 combinations
# ---------------------------------------------------------------------------


def test_build_summarizer_prompt_no_context_no_personality():
    """(a) context=None, personality_active=False → template unchanged."""
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=None, personality_active=False)
    assert result == _SUMMARIZE_PROMPT


def test_build_summarizer_prompt_with_context_no_personality():
    """(b) context present, personality_active=False → template + Additional Context."""
    ctx_text = "Files touched: /foo/bar.py"
    result = _build_summarizer_prompt(
        _SUMMARIZE_PROMPT, context=ctx_text, personality_active=False
    )
    assert result.startswith(_SUMMARIZE_PROMPT)
    assert "## Additional Context" in result
    assert ctx_text in result
    # No personality addendum
    assert _PERSONALITY_COMPACTION_ADDENDUM not in result


def test_build_summarizer_prompt_no_context_with_personality():
    """(c) context=None, personality_active=True → template + personality addendum."""
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=None, personality_active=True)
    assert result.startswith(_SUMMARIZE_PROMPT)
    assert _PERSONALITY_COMPACTION_ADDENDUM in result
    assert "## Additional Context" not in result


def test_build_summarizer_prompt_with_context_and_personality():
    """(d) context + personality → template + context + personality (personality always last)."""
    ctx_text = "Active tasks:\n- [pending] fix bug"
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=ctx_text, personality_active=True)
    assert result.startswith(_SUMMARIZE_PROMPT)
    assert "## Additional Context" in result
    assert ctx_text in result
    assert _PERSONALITY_COMPACTION_ADDENDUM in result
    # Personality comes after context
    ctx_pos = result.index("## Additional Context")
    personality_pos = result.index("Additionally, preserve:")
    assert personality_pos > ctx_pos
