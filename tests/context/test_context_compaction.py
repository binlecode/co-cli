"""Tests for compaction budget resolution and token-triggered compaction."""

import asyncio
import types
from pathlib import Path

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
from tests._frontend import SilentFrontend
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import Settings
from co_cli.config.compaction import CompactionSettings
from co_cli.context.compaction import (
    SUMMARY_MARKER_PREFIX,
    apply_compaction,
    gather_compaction_context,
    proactive_window_processor,
    recover_overflow_history,
    summary_marker,
)
from co_cli.context.orchestrate import _check_output_limits, _TurnState
from co_cli.context.summarization import (
    _PERSONALITY_COMPACTION_ADDENDUM,
    _SUMMARIZE_PROMPT,
    _build_summarizer_prompt,
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend

_CONFIG = make_settings()
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
    group cannot fit under tail_fraction (0.20) * 100_000 = 20_000 tokens.
    """
    # 90_000 > int(100_000 * 0.65) = 65_000 → trigger fires
    msgs = _make_messages(10, last_input_tokens=90_000, body_chars=30_000)
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    ctx = _make_ctx(config)
    result = await proactive_window_processor(ctx, msgs)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# Case 2: Char-estimate fallback when no usage data is available
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_fallback_when_no_usage_data():
    """When no ModelResponse has usage data, latest_response_input_tokens returns 0
    and compaction still triggers correctly via the char-estimate fallback.

    Uses a tiny Ollama budget (llm_num_ctx=30) so the char-estimate
    (~33 tokens from 10 messages) exceeds int(30 * 0.65) = 19.
    """
    msgs_no_usage = _make_messages(10, last_input_tokens=0)
    assert latest_response_input_tokens(msgs_no_usage) == 0

    # Char-estimate fallback: ~135 chars / 4 ≈ 33 tokens > threshold 19 (int(30 * 0.65))
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
    )
    ctx = _make_ctx(config)
    result = await proactive_window_processor(ctx, msgs_no_usage)
    assert len(result) < len(msgs_no_usage)


# ---------------------------------------------------------------------------
# Case 3: Ollama budget branch — compaction uses llm_num_ctx, not default budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_triggers_on_ollama_budget():
    """Ollama: input_tokens=7_200 with llm_num_ctx=8192 triggers compaction.

    budget = 8192 (raw context_window, no reserve subtraction).
    7_200 > int(8192 * 0.65) = 5324 → trigger fires. body_chars sized so each
    group exceeds tail_fraction * 8192 = ~1638 tokens.
    """
    msgs = _make_messages(10, last_input_tokens=7_200, body_chars=3_000)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 8192}),
    )
    assert config.llm.uses_ollama()
    ctx = _make_ctx(config)
    result = await proactive_window_processor(ctx, msgs)
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
            parts=[ToolCallPart(tool_name="file_search", args={}, tool_call_id="c1")],
        )
    ]
    with_args = [
        ModelResponse(
            parts=[ToolCallPart(tool_name="file_search", args=big_args, tool_call_id="c1")],
        )
    ]
    assert estimate_message_tokens(with_args) > estimate_message_tokens(bare)


def test_estimate_counts_list_tool_return():
    """ToolReturnPart.content as list is JSON-serialized and counted (Gap E)."""
    big_list = ["item " + "y" * 200 for _ in range(20)]
    msgs_with_list = [
        ModelRequest(
            parts=[ToolReturnPart(tool_name="file_search", content=big_list, tool_call_id="c1")],
        )
    ]
    msgs_empty = [
        ModelRequest(
            parts=[ToolReturnPart(tool_name="file_search", content=[], tool_call_id="c1")],
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
    # Budget = 100K (anthropic). threshold = 65K. Middle body ~100K tokens.
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
    result = await proactive_window_processor(ctx, msgs)
    assert len(result) < len(msgs), "max() floor should have triggered compaction"


def test_summarize_prompt_active_task_section() -> None:
    """## Active Task is the first static section — appears before ## Goal."""
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=None, personality_active=False)
    assert "## Active Task" in result
    assert result.index("## Active Task") < result.index("## Goal")


def test_summarize_prompt_critical_context_section() -> None:
    """## Critical Context is present and positioned after ## Next Step."""
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=None, personality_active=False)
    assert "## Critical Context" in result
    assert result.index("## Next Step") < result.index("## Critical Context")


def test_summarize_prompt_pending_resolved_sections() -> None:
    """Assembled prompt includes ## Pending User Asks and ## Resolved Questions sections."""
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=None, personality_active=False)
    assert "## Pending User Asks" in result
    assert "## Resolved Questions" in result


def test_summarize_prompt_merge_contract() -> None:
    """Assembled prompt includes the explicit state-transition contract for prior summaries."""
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=None, personality_active=False)
    assert "move to '## Resolved Questions'" in result


def test_summarize_prompt_skip_guard() -> None:
    """Pending User Asks and Resolved Questions both carry a skip-if-none guard."""
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=None, personality_active=False)
    assert "Skip if none" in result


def test_build_summarizer_prompt_keeps_personality_after_context() -> None:
    """When both addenda are present, personality guidance must stay after context."""
    ctx_text = "Active tasks:\n- [pending] fix bug"
    result = _build_summarizer_prompt(_SUMMARIZE_PROMPT, context=ctx_text, personality_active=True)
    ctx_pos = result.index("## Additional Context")
    personality_pos = result.index("Additionally, preserve:")
    assert personality_pos > ctx_pos


# ---------------------------------------------------------------------------
# proactive_window_processor — migrated behavioral coverage from M0 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_processor_fail_open_unusable_budget() -> None:
    """When budget resolves to 0 (no context window known), processor skips and returns history unchanged."""
    msgs = _make_messages(10, body_chars=40_000)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "anthropic", "ctx_token_budget": 0})
    )
    ctx = _make_ctx(config)
    result = await proactive_window_processor(ctx, msgs)
    assert result is msgs


@pytest.mark.asyncio
async def test_proactive_latest_user_turn_survives() -> None:
    """The most recent user message is preserved after proactive compaction."""
    last_user_content = "the final user message that must survive compaction"
    msgs = _make_messages(10, body_chars=40_000)
    msgs.append(ModelRequest(parts=[UserPromptPart(content=last_user_content)]))
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    ctx = _make_ctx(config)
    result = await proactive_window_processor(ctx, msgs)
    assert len(result) < len(msgs)
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


@pytest.mark.asyncio
async def test_proactive_fires_on_reported_tokens() -> None:
    """Proactive fires when provider-reported count exceeds threshold, even if char estimate is below it.

    Simulates a code-heavy session where chars/4 underestimates actual tokens by ~1.5x.
    Budget 100K, threshold = int(100_000 * 0.65) = 65_000.
    Char estimate: ~60_000 tokens (below threshold).
    Reported count: 69_000 (above threshold). Without the max-of-two trigger,
    proactive would not fire. With it, the provider-reported count drives the decision.
    """
    # 10 messages x 24_000 chars = 240_000 chars / 4 = 60_000 tokens (below threshold)
    threshold = int(100_000 * CompactionSettings().compaction_ratio)  # 65_000
    msgs = _make_messages(10, body_chars=24_000, last_input_tokens=threshold + 4_000)
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    ctx = _make_ctx(config)
    assert estimate_message_tokens(msgs) < threshold
    result = await proactive_window_processor(ctx, msgs)
    assert len(result) < len(msgs), (
        "proactive must fire when latest_response_input_tokens exceeds threshold, "
        "even if char estimate alone is below it"
    )


# ---------------------------------------------------------------------------
# TASK-3 regression tests: anti-thrashing gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anti_thrashing_gate_suppresses_proactive_after_low_yield_runs() -> None:
    """Anti-thrashing gate: skips proactive compaction after N low-yield runs.

    Sets the low-yield counter to the proactive_thrash_window value. Gate should
    activate and return msgs unchanged.
    """
    msgs = _make_messages(10, last_input_tokens=0)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
        compaction=CompactionSettings(
            min_proactive_savings=0.10,
            proactive_thrash_window=2,
        ),
    )
    ctx = _make_ctx(config)
    # Simulate two low-yield runs (savings < 10%) — gate should activate.
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 2
    result = await proactive_window_processor(ctx, msgs)
    assert result is msgs


@pytest.mark.asyncio
async def test_anti_thrashing_gate_does_not_suppress_when_window_not_full() -> None:
    """Anti-thrashing gate is inactive when the low-yield counter is below the threshold."""
    msgs = _make_messages(10, last_input_tokens=0)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
        compaction=CompactionSettings(
            min_proactive_savings=0.10,
            proactive_thrash_window=2,
        ),
    )
    ctx = _make_ctx(config)
    # Only one low-yield run — gate must not activate.
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 1
    result = await proactive_window_processor(ctx, msgs)
    # Compaction should still fire (gate inactive)
    assert len(result) < len(msgs)


@pytest.mark.asyncio
async def test_savings_clear_unblocks_gate() -> None:
    """Resetting the low-yield counter (as overflow recovery does) deactivates the gate.

    Gate is active at the threshold. After resetting the counter, the next proactive
    pass must fire — confirming the reset contract is sufficient.
    """
    msgs = _make_messages(10, last_input_tokens=0)
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 30}),
        compaction=CompactionSettings(
            min_proactive_savings=0.10,
            proactive_thrash_window=2,
        ),
    )
    ctx = _make_ctx(config)
    # Populate stale low-yield state that would gate proactive compaction.
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 2
    # Simulate overflow recovery reset.
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0
    # After clear, proactive must fire
    result = await proactive_window_processor(ctx, msgs)
    assert len(result) < len(msgs)


# ---------------------------------------------------------------------------
# Iterative summary — TASK-4 tests for previous_compaction_summary feature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.local
async def test_summarize_messages_iterative_branch_preserves_previous_content() -> None:
    """summarize_messages with previous_summary takes the iterative update path.

    A distinctive token planted in previous_summary must appear in the returned
    summary — the PRESERVE discipline requires the model to carry it forward.
    """
    from tests._ollama import ensure_ollama_warm
    from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

    from co_cli.llm._factory import build_model

    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)
    previous_summary = (
        "## Active Task\nI asked you to implement JWT authentication.\n\n"
        "## Goal\nMigrate from session-based to JWT auth. Decision: use HS512_SENTINEL_TOKEN.\n\n"
        "## Key Decisions\nSigning algorithm chosen: HS512_SENTINEL_TOKEN (unusual for audit).\n\n"
        "## Progress\nIn progress: token middleware implementation."
    )
    messages = [
        _user("Update the middleware to validate JWT tokens."),
        _assistant("Updated the middleware to call validate_token() on each request."),
    ]
    await ensure_ollama_warm(_CONFIG.llm.model, _CONFIG.llm.host)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await summarize_messages(deps, messages, previous_summary=previous_summary)
    assert "HS512_SENTINEL_TOKEN" in result, (
        f"distinctive token from previous_summary absent from iterative update output: {result[:400]}"
    )


@pytest.mark.asyncio
@pytest.mark.local
async def test_previous_summary_written_back_after_successful_compaction() -> None:
    """apply_compaction writes raw summary text (no SUMMARY_MARKER_PREFIX) to previous_compaction_summary."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm._factory import build_model

    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)
    deps.runtime.previous_compaction_summary = "EXISTING_SENTINEL_PRIOR_SUMMARY"
    msgs = _make_messages(6, body_chars=500)
    bounds = (0, len(msgs) - 2, len(msgs) - 2)
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    await ensure_ollama_warm(_CONFIG.llm.model, _CONFIG.llm.host)
    # pytest-timeout=120s is the safety net for the LLM summarization call.
    await apply_compaction(ctx, msgs, bounds, announce=False)
    new_summary = deps.runtime.previous_compaction_summary
    assert new_summary is not None
    assert new_summary != "EXISTING_SENTINEL_PRIOR_SUMMARY", (
        "previous_compaction_summary must be updated after successful compaction"
    )
    assert not new_summary.startswith(SUMMARY_MARKER_PREFIX), (
        "stored summary must be raw template content, not the prefixed in-context marker"
    )


@pytest.mark.asyncio
async def test_previous_summary_unchanged_when_summarizer_gate_closed() -> None:
    """previous_compaction_summary is not modified when the summarizer gate is closed (no model)."""
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG)  # model=None → gate closed
    deps.runtime.previous_compaction_summary = "PRESERVED_VALUE_SENTINEL"
    msgs = _make_messages(6, body_chars=100)
    bounds = (0, len(msgs) - 2, len(msgs) - 2)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    _, summary_text = await apply_compaction(ctx, msgs, bounds, announce=False)
    assert summary_text is None, "no model → static marker, summary_text must be None"
    assert deps.runtime.previous_compaction_summary == "PRESERVED_VALUE_SENTINEL", (
        "prior value must be untouched when summarizer does not run"
    )


def test_gather_compaction_context_suppresses_prior_summaries_when_field_set() -> None:
    """Prior-summary enrichment is suppressed when previous_compaction_summary is non-None."""
    dropped = [summary_marker(5, "## Goal\nRefactor auth — PRIOR_CONTENT_SENTINEL")]

    # Control: field None → prior summary IS gathered as enrichment
    deps_control = CoDeps(shell=ShellBackend(), config=_CONFIG)
    ctx_control = RunContext(deps=deps_control, model=None, usage=RunUsage())
    result_control = gather_compaction_context(ctx_control, dropped)
    assert result_control is not None
    assert "Prior summary" in result_control, (
        "prior summary should appear in enrichment when previous_compaction_summary is None"
    )

    # Test: field non-None → prior summary NOT gathered (would duplicate the iterative prompt)
    deps_set = CoDeps(shell=ShellBackend(), config=_CONFIG)
    deps_set.runtime.previous_compaction_summary = "EXISTING_SUMMARY"
    ctx_set = RunContext(deps=deps_set, model=None, usage=RunUsage())
    result_set = gather_compaction_context(ctx_set, dropped)
    assert result_set is None or "Prior summary:" not in result_set, (
        "prior-summary enrichment must be suppressed when previous_compaction_summary is set"
    )


@pytest.mark.asyncio
async def test_session_commands_reset_previous_compaction_summary() -> None:
    """
    /new and /clear reset previous_compaction_summary to None.
    /compact (empty history) and /resume (no sessions) do not touch the field.
    """
    import tempfile

    from co_cli.commands._types import CommandContext
    from co_cli.commands.clear import _cmd_clear
    from co_cli.commands.compact import _cmd_compact
    from co_cli.commands.new import _cmd_new
    from co_cli.commands.resume import _cmd_resume

    deps = CoDeps(shell=ShellBackend(), config=_CONFIG)

    # /clear resets the field
    deps.runtime.previous_compaction_summary = "PRIOR"
    ctx_clear = CommandContext(message_history=[_user("x")], deps=deps, agent=_AGENT)
    await _cmd_clear(ctx_clear, "")
    assert deps.runtime.previous_compaction_summary is None

    # /new resets the field (non-empty history triggers session rotation)
    deps.runtime.previous_compaction_summary = "PRIOR"
    ctx_new = CommandContext(message_history=[_user("x")], deps=deps, agent=_AGENT)
    await _cmd_new(ctx_new, "")
    assert deps.runtime.previous_compaction_summary is None

    # /compact with empty history early-returns; field is NOT reset
    deps.runtime.previous_compaction_summary = "PRIOR"
    ctx_compact = CommandContext(message_history=[], deps=deps, agent=_AGENT)
    await _cmd_compact(ctx_compact, "")
    assert deps.runtime.previous_compaction_summary == "PRIOR"

    # /resume with no sessions early-returns; field is NOT reset
    deps.runtime.previous_compaction_summary = "PRIOR"
    with tempfile.TemporaryDirectory() as tmpdir:
        deps.sessions_dir = Path(tmpdir)
        ctx_resume = CommandContext(message_history=[], deps=deps, agent=_AGENT)
        await _cmd_resume(ctx_resume, "")
    assert deps.runtime.previous_compaction_summary == "PRIOR"


# ---------------------------------------------------------------------------
# Correctness regression tests: thrash gate reset and savings ratio invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thrash_hint_re_arms_after_or1_reset() -> None:
    """recover_overflow_history (OR-1) resets compaction_thrash_hint_emitted so hint re-emits on next thrash."""
    # body_chars=30_000 → ~15K tokens per group, tail_budget=0.20*100K=20K → planner drops middle groups
    msgs = _make_messages(10, body_chars=30_000)
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    ctx = _make_ctx(config)
    ctx.deps.runtime.compaction_thrash_hint_emitted = True
    ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 2

    result = await recover_overflow_history(ctx, msgs)
    assert result is not None
    assert ctx.deps.runtime.compaction_thrash_hint_emitted is False
    assert ctx.deps.runtime.consecutive_low_yield_proactive_compactions == 0


@pytest.mark.asyncio
async def test_compact_command_resets_thrash_state() -> None:
    """Manual /compact resets the thrash counter and re-arms the hint flag.

    Uses model=None (deps default) so compaction applies a static marker without
    an LLM call. The gate reset must happen regardless of summarizer availability.
    """
    from co_cli.commands._types import CommandContext
    from co_cli.commands.compact import _cmd_compact

    deps = CoDeps(shell=ShellBackend(), config=_CONFIG)
    deps.runtime.consecutive_low_yield_proactive_compactions = 2
    deps.runtime.compaction_thrash_hint_emitted = True

    msgs = _make_messages(4)
    ctx = CommandContext(message_history=msgs, deps=deps, agent=_AGENT)
    result = await _cmd_compact(ctx, "")

    assert result is not None
    assert deps.runtime.consecutive_low_yield_proactive_compactions == 0
    assert deps.runtime.compaction_thrash_hint_emitted is False


@pytest.mark.asyncio
async def test_savings_ratio_uses_local_estimate_only() -> None:
    """Savings ratio uses local token estimate denominator, not provider-reported count.

    The trigger threshold uses max(local, reported) to bias toward earlier compaction.
    The savings ratio must use local-only on both sides (tokens_before_local and
    tokens_after_local) for an apples-to-apples yield comparison. Using the inflated
    reported count as denominator would falsely inflate savings and prevent the thrash
    gate from engaging on genuinely low-yield passes.
    """
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    ctx = _make_ctx(config)
    # Oversized history: local estimate well above threshold; reported count also above.
    msgs = _make_messages(10, body_chars=32_000, last_input_tokens=70_000)
    local_before = estimate_message_tokens(msgs)
    threshold = int(100_000 * config.compaction.compaction_ratio)
    assert local_before > threshold

    result = await proactive_window_processor(ctx, msgs)
    assert len(result) < len(msgs)

    local_after = estimate_message_tokens(result)
    # Compute the expected savings using local-only semantics (what the production code should do).
    expected_savings = (local_before - local_after) / local_before if local_before > 0 else 0.0
    # Counter state must match local-only semantics — not provider-inflated semantics.
    if expected_savings >= config.compaction.min_proactive_savings:
        assert ctx.deps.runtime.consecutive_low_yield_proactive_compactions == 0, (
            "local savings cleared threshold — counter should have reset"
        )
    else:
        assert ctx.deps.runtime.consecutive_low_yield_proactive_compactions == 1, (
            "local savings below threshold — counter should have incremented"
        )


# ---------------------------------------------------------------------------
# TASK-2: _check_output_limits uses latest single-request count, not cumulative sum
# ---------------------------------------------------------------------------


def _make_turn_state(history: list) -> _TurnState:
    """Minimal _TurnState with a fake latest_result that has finish_reason='stop'."""
    fake_result = types.SimpleNamespace(response=types.SimpleNamespace(finish_reason="stop"))
    ts = _TurnState(current_input=None, current_history=history)
    ts.latest_result = fake_result  # type: ignore[assignment]
    return ts


def test_check_output_limits_multi_segment_no_false_positive():
    """Multi-segment turn: only the LAST ModelResponse's input_tokens is used for ratio.

    3 segments: 40K + 45K + 50K = 135K cumulative. With effective_ctx=100K,
    the old code (cumulative sum) would yield ratio=1.35 → false "Context limit reached".
    The fixed code uses latest_response_input_tokens=50K → ratio=0.50 → no alert.
    """
    # supports_context_ratio_tracking() requires Ollama + num_ctx > 0
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 100_000})
    )
    assert config.llm.supports_context_ratio_tracking()
    assert config.llm.effective_num_ctx() == 100_000

    history = [
        _user("turn 1"),
        ModelResponse(parts=[TextPart("a")], usage=RequestUsage(input_tokens=40_000)),
        _user("turn 2"),
        ModelResponse(parts=[TextPart("b")], usage=RequestUsage(input_tokens=45_000)),
        _user("turn 3"),
        ModelResponse(parts=[TextPart("c")], usage=RequestUsage(input_tokens=50_000)),
    ]
    deps = CoDeps(shell=ShellBackend(), config=config)
    frontend = SilentFrontend()
    _check_output_limits(_make_turn_state(history), deps, frontend)
    assert not any("Context limit reached" in s for s in frontend.statuses), (
        f"False positive: got statuses={frontend.statuses!r} — ratio should be 0.50, not 1.35"
    )


def test_check_output_limits_single_segment_over_limit():
    """Single segment over context limit → 'Context limit reached' is emitted."""
    config = make_settings(
        llm=make_settings().llm.model_copy(update={"provider": "ollama", "num_ctx": 100_000})
    )
    assert config.llm.supports_context_ratio_tracking()

    history = [
        _user("question"),
        ModelResponse(parts=[TextPart("answer")], usage=RequestUsage(input_tokens=120_000)),
    ]
    deps = CoDeps(shell=ShellBackend(), config=config)
    frontend = SilentFrontend()
    _check_output_limits(_make_turn_state(history), deps, frontend)
    assert any("Context limit reached" in s for s in frontend.statuses), (
        f"Expected overflow alert not emitted; got statuses={frontend.statuses!r}"
    )
