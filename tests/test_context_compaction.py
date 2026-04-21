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
from co_cli.config._compaction import CompactionSettings
from co_cli.config._core import Settings, settings
from co_cli.context._history import (
    maybe_run_pre_turn_hygiene,
    summarize_history_window,
)
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
    # 90_000 > int(100_000 * 0.75) = 75_000 → trigger fires
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
    (~33 tokens from 10 messages) exceeds int(30 * 0.75) = 22.
    """
    msgs_no_usage = _make_messages(10, last_input_tokens=0)
    assert latest_response_input_tokens(msgs_no_usage) == 0

    # Char-estimate fallback: ~135 chars / 4 ≈ 33 tokens > threshold 25
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
        compaction=CompactionSettings(min_threshold_tokens=0),
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

    budget = 8192 (raw context_window, no reserve subtraction).
    7_200 > int(8192 * 0.75) = 6144 → trigger fires. body_chars sized so each
    group exceeds tail_fraction * 8192 = ~3276 tokens.
    """
    msgs = _make_messages(10, last_input_tokens=7_200, body_chars=3_000)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 8192}),
        compaction=CompactionSettings(min_threshold_tokens=0),
    )
    assert config.llm.uses_ollama()
    ctx = _make_ctx(config)
    result = await summarize_history_window(ctx, msgs)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# resolve_compaction_budget() — pure function, no LLM calls
# ---------------------------------------------------------------------------


def test_budget_gemini_model_spec():
    """Gemini model with context_window=1M → budget = raw 1M (no reserve subtraction)."""
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "gemini"}))
    budget = resolve_compaction_budget(config, 1_048_576)
    assert budget == 1_048_576


def test_budget_ollama_llm_num_ctx_overrides_spec():
    """Ollama: llm_num_ctx overrides context_window from spec (Modelfile is truth)."""
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 32_768})
    )
    budget = resolve_compaction_budget(config, 262_144)
    # llm_num_ctx (32768) overrides spec (262144) → raw 32768
    assert budget == 32_768


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


def test_budget_small_context_window_returns_raw():
    """Small context_window → returns raw value (no reserve subtraction or floor)."""
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "gemini"}))
    budget = resolve_compaction_budget(config, 20_000)
    assert budget == 20_000


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


# ---------------------------------------------------------------------------
# maybe_run_pre_turn_hygiene — TASK-1 / TASK-2 hygiene compaction tests
#
# Budget: ctx_token_budget=100_000 (default). model=None → ctx_window=None → budget=100_000.
# Hygiene threshold: int(100_000 * 0.88) = 88_000 tokens = 352_000 chars.
# Proactive threshold: int(100_000 * 0.75) = 75_000 tokens = 300_000 chars.
# ---------------------------------------------------------------------------

_HYGIENE_BUDGET = 100_000
_HYGIENE_THRESHOLD_TOKENS = int(_HYGIENE_BUDGET * CompactionSettings().hygiene_ratio)
_PROACTIVE_THRESHOLD_TOKENS = int(_HYGIENE_BUDGET * 0.75)


def _make_hygiene_deps(*, ctx_token_budget: int = _HYGIENE_BUDGET) -> CoDeps:
    config = make_settings(
        llm=make_settings().llm.model_copy(
            update={"provider": "anthropic", "ctx_token_budget": ctx_token_budget}
        )
    )
    return CoDeps(shell=ShellBackend(), config=config)


@pytest.mark.asyncio
async def test_pre_turn_hygiene_compacts_oversized_history() -> None:
    """Rough-estimate tokens above HYGIENE_COMPACTION_RATIO * budget triggers pre-turn compaction."""
    # 10 messages x 40_000 chars = 400_000 chars / 4 = 100_000 tokens > 88_000 threshold
    msgs = _make_messages(10, body_chars=40_000)
    assert estimate_message_tokens(msgs) > _HYGIENE_THRESHOLD_TOKENS
    deps = _make_hygiene_deps()
    result = await maybe_run_pre_turn_hygiene(deps, msgs, None)
    assert len(result) < len(msgs)


@pytest.mark.asyncio
async def test_pre_turn_hygiene_no_op_below_threshold() -> None:
    """History well below the hygiene threshold is returned unchanged."""
    # 4 messages x ~20 chars = ~80 chars / 4 = ~20 tokens ≪ 88_000 threshold
    msgs = _make_messages(4)
    assert estimate_message_tokens(msgs) < _HYGIENE_THRESHOLD_TOKENS
    deps = _make_hygiene_deps()
    result = await maybe_run_pre_turn_hygiene(deps, msgs, None)
    assert result is msgs


@pytest.mark.asyncio
async def test_pre_turn_hygiene_no_op_in_proactive_zone() -> None:
    """History in the proactive zone (above 0.75 but below 0.88) is not touched by hygiene.

    The proactive processor (model-call time) handles this range — hygiene must not fire.
    """
    # 10 messages x 32_000 chars = 320_000 chars / 4 = 80_000 tokens
    # 75_000 < 80_000 < 88_000: in proactive zone, below hygiene threshold
    msgs = _make_messages(10, body_chars=32_000)
    estimate = estimate_message_tokens(msgs)
    assert estimate > _PROACTIVE_THRESHOLD_TOKENS
    assert estimate <= _HYGIENE_THRESHOLD_TOKENS
    deps = _make_hygiene_deps()
    result = await maybe_run_pre_turn_hygiene(deps, msgs, None)
    assert result is msgs


@pytest.mark.asyncio
async def test_pre_turn_hygiene_sets_history_compaction_applied() -> None:
    """history_compaction_applied is True after hygiene compaction fires."""
    msgs = _make_messages(10, body_chars=40_000)
    deps = _make_hygiene_deps()
    assert deps.runtime.history_compaction_applied is False
    result = await maybe_run_pre_turn_hygiene(deps, msgs, None)
    assert len(result) < len(msgs)
    assert deps.runtime.history_compaction_applied is True


@pytest.mark.asyncio
async def test_pre_turn_hygiene_no_flag_when_no_compaction() -> None:
    """history_compaction_applied remains False when hygiene does not fire."""
    msgs = _make_messages(4)
    deps = _make_hygiene_deps()
    assert deps.runtime.history_compaction_applied is False
    await maybe_run_pre_turn_hygiene(deps, msgs, None)
    assert deps.runtime.history_compaction_applied is False


@pytest.mark.asyncio
async def test_pre_turn_hygiene_fail_open_unusable_budget() -> None:
    """When budget resolves to 0 (no context window known), hygiene skips and returns history unchanged."""
    msgs = _make_messages(10, body_chars=40_000)
    deps = _make_hygiene_deps(ctx_token_budget=0)
    result = await maybe_run_pre_turn_hygiene(deps, msgs, None)
    assert result is msgs


@pytest.mark.asyncio
async def test_pre_turn_hygiene_latest_user_turn_survives() -> None:
    """The most recent user message is preserved after pre-turn hygiene compaction."""
    last_user_content = "the final user message that must survive compaction"
    msgs = _make_messages(10, body_chars=40_000)
    # Append the final user turn (oversized history + final message)
    msgs.append(ModelRequest(parts=[UserPromptPart(content=last_user_content)]))
    deps = _make_hygiene_deps()
    result = await maybe_run_pre_turn_hygiene(deps, msgs, None)
    assert len(result) < len(msgs)
    # Find last user message in compacted result
    last_user = None
    for msg in reversed(result):
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    last_user = part.content
                    break
        if last_user is not None:
            break
    assert last_user == last_user_content


# ---------------------------------------------------------------------------
# Gap 2 fix: maybe_run_pre_turn_hygiene uses max(estimate, reported_input_tokens)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_turn_hygiene_fires_when_reported_tokens_exceed_threshold() -> None:
    """Hygiene fires when the provider-reported count exceeds the threshold, even if char estimate is below it.

    Simulates a code-heavy session where chars/4 underestimates actual tokens by ~1.5x.
    Char estimate: 80_000 tokens (below 88_000 threshold).
    Reported count: 92_000 tokens (above 88_000 threshold).
    Without the fix, hygiene would not fire. With the fix it must.
    Uses 10 messages so M3 has enough turn groups to compact.
    """
    # 10 messages x 32_000 chars = 320_000 chars / 4 = 80_000 tokens (below threshold)
    msgs = _make_messages(10, body_chars=32_000)
    assert estimate_message_tokens(msgs) < _HYGIENE_THRESHOLD_TOKENS
    deps = _make_hygiene_deps()
    # Pass a provider-reported count that exceeds the threshold
    result = await maybe_run_pre_turn_hygiene(
        deps, msgs, None, reported_input_tokens=_HYGIENE_THRESHOLD_TOKENS + 4_000
    )
    assert len(result) < len(msgs), (
        "hygiene must fire when reported_input_tokens exceeds threshold, "
        "even if char estimate alone is below it"
    )


@pytest.mark.asyncio
async def test_pre_turn_hygiene_no_op_first_turn_zero_reported() -> None:
    """First-turn (no prior usage) passes reported_input_tokens=0 — falls back to char estimate.

    This is the backwards-compatible default: no regression from adding the parameter.
    History is small (well below threshold), so hygiene must not fire.
    """
    msgs = _make_messages(4)
    assert estimate_message_tokens(msgs) < _HYGIENE_THRESHOLD_TOKENS
    deps = _make_hygiene_deps()
    result = await maybe_run_pre_turn_hygiene(deps, msgs, None, reported_input_tokens=0)
    assert result is msgs


# ---------------------------------------------------------------------------
# TASK-3 regression tests: threshold floor + anti-thrashing gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_threshold_floor_prevents_compaction_on_small_context() -> None:
    """min_threshold_tokens floor blocks compaction when budget is tiny and tokens < floor.

    With default min_threshold_tokens=32_000 and num_ctx=30, the effective threshold
    is max(int(30*0.75), 32_000) = 32_000. A tiny message set (~33 tokens) is well
    below that floor — compaction must not fire.
    """
    msgs = _make_messages(10, last_input_tokens=0)
    # Default min_threshold_tokens=32_000 — floor should prevent compaction
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30})
    )
    ctx = _make_ctx(config)
    result = await summarize_history_window(ctx, msgs)
    # Floor prevents compaction — result returned unchanged
    assert result is msgs


@pytest.mark.asyncio
async def test_anti_thrashing_gate_suppresses_proactive_after_low_yield_runs() -> None:
    """Anti-thrashing gate: skips proactive compaction when recent N runs all had < min savings.

    Sets recent_proactive_savings to two entries both below min_proactive_savings (0.10),
    and proactive_thrash_window=2. Gate should activate and return msgs unchanged.
    """
    msgs = _make_messages(10, last_input_tokens=0)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
        compaction=CompactionSettings(
            min_threshold_tokens=0,
            min_proactive_savings=0.10,
            proactive_thrash_window=2,
        ),
    )
    ctx = _make_ctx(config)
    # Simulate two low-yield runs (savings < 10%) — gate should activate
    ctx.deps.runtime.recent_proactive_savings = [0.02, 0.03]
    result = await summarize_history_window(ctx, msgs)
    assert result is msgs


@pytest.mark.asyncio
async def test_anti_thrashing_gate_does_not_suppress_when_window_not_full() -> None:
    """Anti-thrashing gate is inactive when fewer than proactive_thrash_window runs have been recorded."""
    msgs = _make_messages(10, last_input_tokens=0)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
        compaction=CompactionSettings(
            min_threshold_tokens=0,
            min_proactive_savings=0.10,
            proactive_thrash_window=2,
        ),
    )
    ctx = _make_ctx(config)
    # Only one low-yield run — window not full, gate must not activate
    ctx.deps.runtime.recent_proactive_savings = [0.02]
    result = await summarize_history_window(ctx, msgs)
    # Compaction should still fire (gate inactive)
    assert len(result) < len(msgs)


@pytest.mark.asyncio
async def test_hygiene_not_blocked_by_anti_thrashing_gate() -> None:
    """Pre-turn hygiene compaction fires even when the anti-thrashing gate is active.

    Gate is active with two low-yield entries. Hygiene must still compact because
    maybe_run_pre_turn_hygiene clears the savings ring before calling summarize_history_window.
    """
    # 10 messages x 40_000 chars = 400_000 chars / 4 = 100_000 tokens > 88_000 threshold
    msgs = _make_messages(10, body_chars=40_000)
    # Active gate: two low-yield runs below min_proactive_savings=0.10
    deps = _make_hygiene_deps()
    deps.runtime.recent_proactive_savings = [0.02, 0.03]
    result = await maybe_run_pre_turn_hygiene(deps, msgs, None)
    # Gate must not block hygiene — compaction must fire
    assert len(result) < len(msgs)
    # Stale gate state is gone: original [0.02, 0.03] values are cleared before compaction.
    # The ring may contain one new entry from the hygiene-triggered compaction savings.
    assert 0.02 not in deps.runtime.recent_proactive_savings
    assert 0.03 not in deps.runtime.recent_proactive_savings


@pytest.mark.asyncio
async def test_savings_clear_unblocks_gate() -> None:
    """Clearing recent_proactive_savings (as hygiene and overflow do) deactivates the gate.

    Gate is active with [0.02, 0.03]. After clearing (what orchestrate.py does post-hygiene),
    the next proactive pass must fire — confirming the reset contract is sufficient.
    """
    msgs = _make_messages(10, last_input_tokens=0)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
        compaction=CompactionSettings(
            min_threshold_tokens=0,
            min_proactive_savings=0.10,
            proactive_thrash_window=2,
        ),
    )
    ctx = _make_ctx(config)
    # Populate stale savings that would gate proactive compaction
    ctx.deps.runtime.recent_proactive_savings = [0.02, 0.03]
    # Simulate orchestrate.py post-hygiene/overflow reset
    ctx.deps.runtime.recent_proactive_savings.clear()
    # After clear, proactive must fire
    result = await summarize_history_window(ctx, msgs)
    assert len(result) < len(msgs)
