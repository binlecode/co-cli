"""Tests for compaction budget resolution and token-triggered compaction."""

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage, RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import Settings, settings
from co_cli.context._history import summarize_history_window
from co_cli.context.summarization import (
    _PERSONALITY_COMPACTION_ADDENDUM,
    _SUMMARIZE_PROMPT,
    _build_summarizer_prompt,
    estimate_message_tokens,
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


def _make_messages(n: int, last_input_tokens: int = 0, body_chars: int = 0) -> list:
    """Alternating user/assistant messages; last assistant has specified token usage.

    ``body_chars`` — optional per-message padding so the planner's own estimator
    sees real token weight (needed for compaction tests since the planner walks
    groups by estimated size, not by message count).
    """
    msgs = []
    padding = "x" * body_chars if body_chars else ""
    for i in range(n // 2):
        msgs.append(_user(f"user turn {i} {padding}"))
        tokens = last_input_tokens if i == (n // 2 - 1) else 0
        msgs.append(_assistant(f"assistant turn {i} {padding}", input_tokens=tokens))
    if n % 2:
        msgs.append(_user(f"user turn {n // 2} {padding}"))
    return msgs


# ---------------------------------------------------------------------------
# Case 1: Cloud provider real usage triggers compaction (> 85% of default budget)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_triggers_on_real_input_tokens():
    """Reported input_tokens=90_000 triggers compaction; planner drops groups.

    The planner walks groups by its own estimator, so the message bodies must
    carry real char weight for the walk to find anything to drop. body_chars
    ≈ 30_000 per message makes each group ≈ 15_000 tokens — more than one
    group cannot fit under tail_fraction (0.40) * 100_000 = 40_000 tokens.
    """
    # 90_000 > int(100_000 * 0.85) = 85_000 → trigger fires
    msgs = _make_messages(10, last_input_tokens=90_000, body_chars=30_000)
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
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30})
    )
    ctx = _make_ctx(config)
    result = await summarize_history_window(ctx, msgs_no_usage)
    assert len(result) < len(msgs_no_usage)


# ---------------------------------------------------------------------------
# Case 3: Ollama budget branch — compaction uses llm_num_ctx, not default budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_triggers_on_ollama_budget():
    """Ollama: input_tokens=7_200 with llm_num_ctx=8192 triggers compaction.

    budget = max(8192 - 16384, 8192 // 2) = 4096 (under-16K floor branch).
    7_200 > int(4096 * 0.85) = 3481 → trigger fires. body_chars sized so each
    group exceeds tail_fraction * 4096 = ~1638 tokens.
    """
    msgs = _make_messages(10, last_input_tokens=7_200, body_chars=3_000)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 8192})
    )
    assert config.llm.uses_ollama()
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
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 32_768})
    )
    budget = resolve_compaction_budget(config, 262_144)
    # llm_num_ctx (32768) overrides spec (262144), so effective ctx_window = 32768
    # max(32768 - 16384, 32768 // 2) = max(16384, 16384) = 16384
    assert budget == 32_768 // 2


def test_budget_ollama_no_spec_falls_back_to_llm_num_ctx():
    """Ollama with no resolved context_window → falls back to llm_num_ctx."""
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 32_768})
    )
    budget = resolve_compaction_budget(config, None)
    assert budget == 32_768


def test_budget_no_context_window_returns_default():
    """No context_window and no num_ctx → config.llm.ctx_token_budget."""
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "gemini"}))
    budget = resolve_compaction_budget(config, None)
    assert budget == config.llm.ctx_token_budget


def test_budget_floor_prevents_negative():
    """Small context_window → floor at context_window//2."""
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "gemini"}))
    # context_window=20000: max(20000 - 16384, 10000) = max(3616, 10000) = 10000
    budget = resolve_compaction_budget(config, 20_000)
    assert budget == 10_000


# ---------------------------------------------------------------------------
# _build_summarizer_prompt() — pure function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("context", "personality_active", "expects_context", "expects_personality"),
    [
        (None, False, False, False),
        ("Files touched: /foo/bar.py", False, True, False),
        (None, True, False, True),
        ("Active tasks:\n- [pending] fix bug", True, True, True),
    ],
    ids=[
        "no_context_no_personality",
        "context_only",
        "personality_only",
        "context_and_personality",
    ],
)
def test_build_summarizer_prompt_variants(
    context: str | None,
    personality_active: bool,
    expects_context: bool,
    expects_personality: bool,
) -> None:
    """Prompt builder should append context and personality addenda only when requested."""
    result = _build_summarizer_prompt(
        _SUMMARIZE_PROMPT,
        context=context,
        personality_active=personality_active,
    )

    if not expects_context and not expects_personality:
        assert result == _SUMMARIZE_PROMPT
        return

    assert result.startswith(_SUMMARIZE_PROMPT)
    if expects_context:
        assert "## Additional Context" in result
        assert context is not None
        assert context in result
    else:
        assert "## Additional Context" not in result

    if expects_personality:
        assert _PERSONALITY_COMPACTION_ADDENDUM in result
    else:
        assert _PERSONALITY_COMPACTION_ADDENDUM not in result


# ---------------------------------------------------------------------------
# estimate_message_tokens — TASK-2 estimator hardening
# ---------------------------------------------------------------------------


def test_estimate_counts_tool_call_args():
    """ToolCallPart.args JSON is counted (Gap E). Tool-heavy transcripts now trigger accurately."""
    big_args = {"query": "x" * 2000}
    bare = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="file_grep", args={}, tool_call_id="c1")],
        )
    ]
    with_args = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="file_grep", args=big_args, tool_call_id="c1")],
        )
    ]
    assert estimate_message_tokens(with_args) > estimate_message_tokens(bare)


def test_estimate_counts_list_tool_return():
    """ToolReturnPart.content as list is JSON-serialized and counted (Gap E)."""
    big_list = ["item " + "y" * 200 for _ in range(20)]
    msgs_with_list = [
        ModelRequest(
            parts=[ToolReturnPart(tool_name="file_grep", content=big_list, tool_call_id="c1")],
        )
    ]
    msgs_empty = [
        ModelRequest(
            parts=[ToolReturnPart(tool_name="file_grep", content=[], tool_call_id="c1")],
        )
    ]
    assert estimate_message_tokens(msgs_with_list) > estimate_message_tokens(msgs_empty)
    # Sanity: the list content really generates significant token weight.
    assert estimate_message_tokens(msgs_with_list) > 500


@pytest.mark.asyncio
async def test_trigger_uses_max_floor():
    """Stale low reported count cannot suppress trigger when estimate is higher (max-floor semantics).

    Placing the huge body in a MIDDLE user turn so it's in the droppable range
    (not the head). Old fallback logic (``reported if reported > 0 else estimate``)
    would pick reported=100 → no trigger. max() floor picks estimate (~100K) →
    trigger fires and planner drops the heavy middle group.
    """
    # Budget = 100K (anthropic). threshold = 85K. Middle body ~100K tokens.
    big_body = "x" * 400_000
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="turn 0")]),
        ModelResponse(parts=[TextPart(content="reply 0")]),
        ModelRequest(parts=[UserPromptPart(content=big_body + " turn 1")]),
        ModelResponse(parts=[TextPart(content="reply 1")]),
        ModelRequest(parts=[UserPromptPart(content="turn 2")]),
        ModelResponse(parts=[TextPart(content="reply 2")]),
        ModelRequest(parts=[UserPromptPart(content="turn 3")]),
        ModelResponse(parts=[TextPart(content="reply 3")], usage=RequestUsage(input_tokens=100)),
    ]
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    ctx = _make_ctx(config)
    result = await summarize_history_window(ctx, msgs)
    assert len(result) < len(msgs), "max() floor should have triggered compaction"


def test_build_summarizer_prompt_keeps_personality_after_context() -> None:
    """When both addenda are present, personality guidance must stay after context."""
    ctx_text = "Active tasks:\n- [pending] fix bug"
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=ctx_text, personality_active=True)
    ctx_pos = result.index("## Additional Context")
    personality_pos = result.index("Additionally, preserve:")
    assert personality_pos > ctx_pos
