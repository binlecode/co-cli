"""Tests for compaction budget resolution and token-triggered compaction."""

import asyncio
import types
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage, RunUsage
from tests._frontend import SilentFrontend
from tests._settings import SETTINGS as _CONFIG
from tests._settings import TEST_LLM, make_settings
from tests._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS

from co_cli.agent.core import build_agent
from co_cli.config.compaction import CompactionSettings
from co_cli.config.core import Settings
from co_cli.context._tool_result_markers import is_cleared_marker
from co_cli.context.compaction import (
    COMPACTABLE_KEEP_RECENT,
    STATIC_MARKER_PREFIX,
    SUMMARY_MARKER_PREFIX,
    _is_valid_summary,
    apply_compaction,
    evict_old_tool_results,
    gather_compaction_context,
    plan_compaction_boundaries,
    proactive_window_processor,
    recover_overflow_history,
    summary_marker,
)
from co_cli.context.orchestrate import _check_output_limits, _TurnState
from co_cli.context.summarization import (
    _PERSONALITY_COMPACTION_ADDENDUM,
    _SUMMARIZE_PROMPT,
    _build_iterative_template,
    _build_summarizer_prompt,
    estimate_message_tokens,
    latest_response_input_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend

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


def test_summarize_prompt_no_first_sentence_constraint() -> None:
    """Prompt must not contain the 'I asked you' first-sentence constraint.

    That constraint caused the model to echo the summarizer instruction as the
    session task when history contained no real user task (prompt bleed). Task
    identification is now delegated entirely to ## Active Task. Regression lock
    against the constraint silently re-appearing.
    """
    assert "I asked you" not in _SUMMARIZE_PROMPT
    assert "MUST start with" not in _SUMMARIZE_PROMPT
    assert "Do NOT include any preamble" in _SUMMARIZE_PROMPT


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


def test_summarize_prompt_progress_structure() -> None:
    """Progress sections are four structured ## headings in temporal order."""
    prompt = _SUMMARIZE_PROMPT
    assert "## Completed Actions" in prompt
    assert "## In Progress" in prompt
    assert "## Remaining Work" in prompt
    assert "## Working Set" in prompt
    ca = prompt.index("## Completed Actions")
    ip = prompt.index("## In Progress")
    rw = prompt.index("## Remaining Work")
    ws = prompt.index("## Working Set")
    assert ca < ip < rw < ws, "sections must appear in temporal order"
    assert "## Progress\n" not in prompt, "old flat ## Progress section must be removed"
    assert "[tool: name]" in prompt, "tool attribution hint must be present"
    # Constraint 12: other sections not moved
    assert prompt.index("## Active Task") < prompt.index("## Goal")
    assert prompt.index("## Errors & Fixes") < ca


def test_iterative_template_references_only_existing_sections() -> None:
    """Every quoted section name in the iterative preamble maps to a ## heading in _SUMMARIZE_PROMPT."""
    import re

    output = _build_iterative_template("prev")
    assert "Active State" not in output, "orphaned 'Active State' reference must be removed"
    assert "Completed Actions" in output
    assert "In Progress" in output
    assert "Resolved Questions" in output
    # Scope regex to the preamble only — not the appended _SUMMARIZE_PROMPT (which has its own quotes)
    preamble = output[: output.index(_SUMMARIZE_PROMPT)]
    quoted_sections = re.findall(r"'([^']+)'", preamble)
    for section in quoted_sections:
        # Quoted names may already include the ## prefix
        heading = section if section.startswith("## ") else f"## {section}"
        assert heading in _SUMMARIZE_PROMPT, (
            f"iterative template references '{section}' which does not exist in _SUMMARIZE_PROMPT"
        )


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
async def test_summarize_messages_iterative_branch_preserves_previous_content() -> None:
    """summarize_messages with previous_summary takes the iterative update path.

    A distinctive token planted in previous_summary must appear in the returned
    summary — the PRESERVE discipline requires the model to carry it forward.
    """
    from tests._ollama import ensure_ollama_warm
    from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

    from co_cli.llm.factory import build_model

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
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    # Iterative template embeds the previous summary — larger prompt than a bare-context
    # call; use LLM_TOOL_CONTEXT_TIMEOUT_SECS (20s) for the prefill budget.
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        result = await summarize_messages(deps, messages, previous_summary=previous_summary)
    assert "HS512_SENTINEL_TOKEN" in result, (
        f"distinctive token from previous_summary absent from iterative update output: {result[:400]}"
    )


@pytest.mark.asyncio
async def test_previous_summary_written_back_after_successful_compaction() -> None:
    """apply_compaction writes raw summary text (no SUMMARY_MARKER_PREFIX) to previous_compaction_summary."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm.factory import build_model

    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)
    deps.runtime.previous_compaction_summary = "EXISTING_SENTINEL_PRIOR_SUMMARY"
    msgs = _make_messages(6, body_chars=500)
    bounds = (0, len(msgs) - 2, len(msgs) - 2)
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
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

    from co_cli.commands.clear import _cmd_clear
    from co_cli.commands.compact import _cmd_compact
    from co_cli.commands.new import _cmd_new
    from co_cli.commands.resume import _cmd_resume
    from co_cli.commands.types import CommandContext

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
    from co_cli.commands.compact import _cmd_compact
    from co_cli.commands.types import CommandContext

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


# ---------------------------------------------------------------------------
# TASK-7 — Summary validator
# ---------------------------------------------------------------------------


def test_is_valid_summary_rejects_empty_and_whitespace() -> None:
    """_is_valid_summary returns False for empty/whitespace-only strings and None."""
    assert not _is_valid_summary(None)
    assert not _is_valid_summary("")
    assert not _is_valid_summary("   ")
    assert not _is_valid_summary("\t\n")


def test_is_valid_summary_accepts_non_empty_strings() -> None:
    """_is_valid_summary returns True for any non-empty string, regardless of structure."""
    assert _is_valid_summary("Good progress so far.")
    assert _is_valid_summary("## Active Task\nRefactor auth module.")
    assert _is_valid_summary("x")


def test_gather_compaction_context_scoped_to_dropped() -> None:
    """File paths are extracted only from the dropped slice (Gap M).

    Paths outside the dropped slice must not appear in the enrichment result
    since gather_compaction_context receives only the dropped messages as its source.
    """
    dropped = [
        _user("mid"),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="file_read", args={"file_path": "/mid.py"}, tool_call_id="m1"
                )
            ]
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="file_read", content="z", tool_call_id="m1")]
        ),
        _assistant("mid"),
    ]
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    result = gather_compaction_context(ctx, dropped)
    assert result is not None
    assert "/mid.py" in result
    assert "/head.py" not in result
    assert "/tail.py" not in result


def test_gather_compaction_context_cap() -> None:
    """Enrichment result never exceeds _CONTEXT_MAX_CHARS = 4000 chars."""
    from co_cli.context._compaction_markers import _CONTEXT_MAX_CHARS

    big_todos = [{"content": "x" * 500, "status": "pending"} for _ in range(20)]
    many_file_calls = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="file_read",
                args={"file_path": f"/a/b/c/d/e/file_{i:03d}.py"},
                tool_call_id=f"c{i}",
            )
            for i in range(20)
        ]
    )
    dropped = [_user("work"), many_file_calls, _assistant("done")]
    session = CoSessionState()
    session.session_todos = big_todos
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, session=session)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    result = gather_compaction_context(ctx, dropped)
    if result is not None:
        assert len(result) <= _CONTEXT_MAX_CHARS


@pytest.mark.asyncio
async def test_apply_compaction_static_marker_when_no_model() -> None:
    """When the summarizer gate is closed (no model), apply_compaction produces a static marker.

    This exercises the same end-state as the empty-summary validator path: both
    result in summary_text=None, previous_compaction_summary unchanged, and
    the compaction marker starting with STATIC_MARKER_PREFIX.
    """
    from pydantic_ai.messages import UserPromptPart

    deps = CoDeps(shell=ShellBackend(), config=_CONFIG)
    sentinel = "SENTINEL_TASK7"
    deps.runtime.previous_compaction_summary = sentinel
    msgs = _make_messages(6, body_chars=100)
    bounds = (0, len(msgs) - 2, len(msgs) - 2)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    result, summary_text = await apply_compaction(ctx, msgs, bounds, announce=False)

    assert summary_text is None, "no model → static marker, summary_text must be None"
    assert deps.runtime.previous_compaction_summary == sentinel, (
        "previous_compaction_summary must be unchanged when summarizer does not run"
    )
    # The marker message is the first element after the (empty) head.
    marker_msg = result[0]
    assert isinstance(marker_msg, ModelRequest)
    marker_content = next(
        (
            p.content
            for p in marker_msg.parts
            if isinstance(p, UserPromptPart) and isinstance(p.content, str)
        ),
        None,
    )
    assert marker_content is not None
    assert marker_content.startswith(STATIC_MARKER_PREFIX), (
        f"Expected STATIC_MARKER_PREFIX, got: {marker_content[:80]!r}"
    )
    assert not marker_content.startswith(SUMMARY_MARKER_PREFIX), (
        "static marker must not use SUMMARY_MARKER_PREFIX"
    )


# ---------------------------------------------------------------------------
# TASK-1 — Stale provider-token cross-turn suppression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_compaction_estimate_set_after_apply_compaction() -> None:
    """apply_compaction sets post_compaction_token_estimate and message_count_at_last_compaction."""
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG)
    msgs = _make_messages(6, body_chars=100)
    bounds = (0, len(msgs) - 2, len(msgs) - 2)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    result, _ = await apply_compaction(ctx, msgs, bounds, announce=False)

    assert deps.runtime.post_compaction_token_estimate is not None
    assert deps.runtime.post_compaction_token_estimate > 0
    assert deps.runtime.message_count_at_last_compaction == len(result)


@pytest.mark.asyncio
async def test_stale_token_suppression_at_turn_boundary() -> None:
    """proactive_window_processor uses the local estimate, not the stale ModelResponse count.

    After compaction, the preserved tail's last ModelResponse carries the pre-compaction
    input_tokens count. Without suppression, max(local, stale) picks the stale figure and
    fires a spurious compaction. With the cross-turn estimate, reported = estimate (small),
    so max(local, estimate) stays below threshold and no compaction fires.
    """
    # Budget=100K; threshold=65K. Body chars produce ~2K local estimate after compaction.
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    deps = CoDeps(shell=ShellBackend(), config=config)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    # Simulate having just compacted: set a small estimate (post-compaction context is small)
    # but leave a stale 80K reported count in the last ModelResponse.
    small_estimate = 2_000
    deps.runtime.post_compaction_token_estimate = small_estimate
    deps.runtime.message_count_at_last_compaction = 4

    # History at turn N+1 boundary: only 4 messages (estimate still active — no new pair yet).
    msgs = [
        _user("turn 0"),
        _assistant("reply 0"),
        _user("turn 1"),
        ModelResponse(
            parts=[TextPart(content="reply 1")],
            usage=RequestUsage(input_tokens=80_000),
        ),
    ]
    assert len(msgs) == 4

    result = await proactive_window_processor(ctx, msgs)
    # Stale 80K count must be suppressed; local estimate (~1K) + reported estimate (~2K)
    # both well below 65K threshold — no compaction fired.
    assert result is msgs, (
        "Stale 80K reported count must be suppressed by post_compaction_token_estimate; "
        "no compaction should fire at the turn boundary"
    )
    # Estimate still active (len(msgs)==4 == count, not >= count+2)
    assert deps.runtime.post_compaction_token_estimate == small_estimate


@pytest.mark.asyncio
async def test_stale_suppression_clears_after_fresh_response_lands() -> None:
    """Estimate is cleared when a fresh ModelRequest+ModelResponse pair arrives.

    len(messages) >= message_count_at_last_compaction + 2 signals that at least
    one new request/response cycle has completed and the provider count is fresh.
    """
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    deps = CoDeps(shell=ShellBackend(), config=config)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    deps.runtime.post_compaction_token_estimate = 2_000
    deps.runtime.message_count_at_last_compaction = 4

    # 6 messages = count(4) + 2 → fresh provider count available; estimate should clear.
    msgs = [
        _user("turn 0"),
        _assistant("reply 0"),
        _user("turn 1"),
        _assistant("reply 1"),
        _user("turn 2"),
        ModelResponse(
            parts=[TextPart(content="reply 2")],
            usage=RequestUsage(input_tokens=10_000),
        ),
    ]
    assert len(msgs) == 6

    await proactive_window_processor(ctx, msgs)
    # Estimate must be cleared once the fresh pair lands.
    assert deps.runtime.post_compaction_token_estimate is None
    assert deps.runtime.message_count_at_last_compaction is None


@pytest.mark.asyncio
async def test_session_commands_reset_stale_suppression_fields() -> None:
    """/new and /clear reset post_compaction_token_estimate and message_count_at_last_compaction."""
    from co_cli.commands.clear import _cmd_clear
    from co_cli.commands.new import _cmd_new
    from co_cli.commands.types import CommandContext

    deps = CoDeps(shell=ShellBackend(), config=_CONFIG)
    agent = _AGENT

    deps.runtime.post_compaction_token_estimate = 5_000
    deps.runtime.message_count_at_last_compaction = 10
    ctx_clear = CommandContext(message_history=[_user("x")], deps=deps, agent=agent)
    await _cmd_clear(ctx_clear, "")
    assert deps.runtime.post_compaction_token_estimate is None
    assert deps.runtime.message_count_at_last_compaction is None

    deps.runtime.post_compaction_token_estimate = 5_000
    deps.runtime.message_count_at_last_compaction = 10
    ctx_new = CommandContext(message_history=[_user("x")], deps=deps, agent=agent)
    await _cmd_new(ctx_new, "")
    assert deps.runtime.post_compaction_token_estimate is None
    assert deps.runtime.message_count_at_last_compaction is None


@pytest.mark.asyncio
async def test_three_turn_boundary_no_spurious_compaction() -> None:
    """End-to-end: compaction in turn N must not retrigger at turn N+1 boundary.

    Reproduces the bug TASK-1 fixes: after compaction in turn N, the preserved
    tail's last ModelResponse carries the pre-compaction input_tokens. Without
    suppression, turn N+1's first proactive pass picks max(local, stale=80K) and
    fires a redundant compaction → low yield → consecutive_low_yield counter
    increments. Two such turn boundaries trip the anti-thrash gate.

    With the fix: post_compaction_token_estimate suppresses the stale figure at
    the N+1 boundary; once a fresh ModelResponse lands the estimate auto-clears.
    """
    config = make_settings(llm=make_settings().llm.model_copy(update={"provider": "anthropic"}))
    deps = CoDeps(shell=ShellBackend(), config=config)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    # ----- Turn N: simulate post-compaction state -----
    # Synthesize what apply_compaction would leave: a small post-compaction history
    # whose last ModelResponse still carries a pre-compaction 80K input_tokens.
    post_compacted: list[ModelMessage] = [
        _user("compaction marker stand-in"),
        ModelResponse(
            parts=[TextPart(content="reply 1")],
            usage=RequestUsage(input_tokens=80_000),
        ),
    ]
    deps.runtime.post_compaction_token_estimate = estimate_message_tokens(post_compacted)
    deps.runtime.message_count_at_last_compaction = len(post_compacted)
    deps.runtime.compaction_applied_this_turn = True

    # ----- Turn N → N+1 boundary: orchestrator calls reset_for_turn() -----
    deps.runtime.reset_for_turn()
    assert not deps.runtime.compaction_applied_this_turn
    # Cross-turn fields survive the reset.
    assert deps.runtime.post_compaction_token_estimate is not None
    assert deps.runtime.message_count_at_last_compaction == 2

    # ----- Turn N+1: new user prompt, before first LLM call -----
    turn_n1_msgs = [*post_compacted, _user("turn N+1 prompt")]
    assert len(turn_n1_msgs) == deps.runtime.message_count_at_last_compaction + 1
    low_yield_before = deps.runtime.consecutive_low_yield_proactive_compactions

    result = await proactive_window_processor(ctx, turn_n1_msgs)

    # Stale 80K must be suppressed → no compaction at boundary → no low-yield increment.
    assert result is turn_n1_msgs, "no compaction should fire at the N+1 boundary"
    assert deps.runtime.consecutive_low_yield_proactive_compactions == low_yield_before
    # Estimate still active (count + 1 < count + 2).
    assert deps.runtime.post_compaction_token_estimate is not None

    # ----- Turn N+1 LLM call lands; new ModelResponse appended -----
    turn_n1_msgs.append(
        ModelResponse(
            parts=[TextPart(content="reply 2")],
            usage=RequestUsage(input_tokens=10_000),
        )
    )
    assert len(turn_n1_msgs) == deps.runtime.message_count_at_last_compaction + 2

    # ----- Turn N+1 → N+2 boundary -----
    deps.runtime.reset_for_turn()
    turn_n2_msgs = [*turn_n1_msgs, _user("turn N+2 prompt")]

    result = await proactive_window_processor(ctx, turn_n2_msgs)

    # Estimate auto-cleared once fresh pair landed; trigger now uses the new 10K count.
    assert deps.runtime.post_compaction_token_estimate is None
    assert deps.runtime.message_count_at_last_compaction is None
    assert result is turn_n2_msgs, "no compaction at N+2 boundary either (10K << threshold)"
    # Two clean boundaries → counter still at zero, anti-thrash gate stays disarmed.
    assert deps.runtime.consecutive_low_yield_proactive_compactions == 0


# ---------------------------------------------------------------------------
# Helpers shared by the functional compaction tests below
# ---------------------------------------------------------------------------


def _tool_call(name: str, args: dict, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)])


def _tool_return(name: str, content: str, call_id: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)]
    )


def _analysis(topic: str, extra: str = "") -> str:
    return (
        f"I've analyzed {topic}. The authentication flow starts at the login endpoint. "
        f"The current implementation uses Django's session framework via SessionMiddleware. "
        f"Token-based auth would eliminate the session table dependency entirely. "
        f"The CSRF protection relies on session cookies — JWT migration needs a replacement. "
        f"Implementation: HS256 algorithm, 15-minute access token TTL, HttpOnly refresh cookie, "
        f"Redis token blacklist, rate limiting on the token endpoint, dual-auth middleware for "
        f"zero-downtime migration. Security: jti claim + Redis blacklist prevents token replay. "
        f"{extra}"
        f"I'll proceed with the next file to build the full picture before making changes."
    )


def _fake_file(name: str, lines: int = 30) -> str:
    return "\n".join(
        f"# {name} line {i}: {'def ' if i % 10 == 0 else '    '}handler_{i}(request): pass"
        for i in range(lines)
    )


def _extract_section(summary: str, section_name: str) -> str:
    header = f"## {section_name}"
    start = summary.find(header)
    if start == -1:
        return ""
    content_start = summary.find("\n", start)
    if content_start == -1:
        return ""
    content_start += 1
    next_header = summary.find("\n## ", content_start)
    return (
        summary[content_start:next_header] if next_header != -1 else summary[content_start:]
    ).strip()


def _has_verbatim_anchor(summary_text: str, source_messages: list[ModelMessage]) -> bool:
    # Both "Next Step" and "Active Task" independently satisfy the verbatim-anchor contract.
    # Check them independently so a reformatted ## Next Step (e.g. backtick-wrapped tokens)
    # doesn't shadow a valid ## Active Task verbatim copy.
    sections = [
        s
        for s in (
            _extract_section(summary_text, "Next Step"),
            _extract_section(summary_text, "Active Task"),
        )
        if s
    ]
    if not sections:
        return False
    recent_texts = " ".join(
        p.content
        for m in source_messages[-3:]
        for p in m.parts
        if (isinstance(p, UserPromptPart) and isinstance(p.content, str))
        or (isinstance(p, TextPart) and isinstance(p.content, str))
    )
    return any(
        section[i : i + 20] in recent_texts
        for section in sections
        for i in range(len(section) - 20 + 1)
    )


# ---------------------------------------------------------------------------
# Circuit breaker: static marker when skip_count ≥ 3
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Full chain P1→P5 with LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_full_chain_p1_to_p5_llm() -> None:
    """P1 clears old tool results, P5 LLM summarizer fires; marker count matches dropped count."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.context.compaction import (
        plan_compaction_boundaries,
    )
    from co_cli.llm.factory import build_model

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    N_READ = 10
    history: list[ModelMessage] = [
        _user("Refactor auth from sessions to JWT."),
        ModelResponse(parts=[TextPart(content=_analysis("project structure", "Starting.\n\n"))]),
    ]
    files = [
        f"auth/{n}.py"
        for n in [
            "views",
            "middleware",
            "tokens",
            "permissions",
            "decorators",
            "backends",
            "serializers",
            "signals",
            "utils",
            "constants",
        ]
    ]
    for i, fname in enumerate(files):
        cid = f"rf{i}"
        history += [
            _user(f"Read {fname}"),
            _tool_call("file_read", {"file_path": fname}, cid),
            _tool_return("file_read", _fake_file(fname, 20 + i * 3), cid),
            ModelResponse(parts=[TextPart(content=_analysis(fname))]),
        ]
    history += [_user("Status?"), _assistant("Views and middleware done. Tests remain.")]

    from co_cli.deps import CoSessionState as _CoSessionState

    session = _CoSessionState()
    session.session_todos = [{"content": "Add PyJWT to requirements", "status": "pending"}]
    # Small num_ctx ensures the test history exceeds the tail budget (tail_fraction=0.20)
    # so plan_compaction_boundaries always returns valid bounds regardless of model spec.
    cfg = make_settings(llm=make_settings().llm.model_copy(update={"num_ctx": 8192}))
    llm_model = build_model(cfg.llm)
    deps = CoDeps(shell=ShellBackend(), config=cfg, model=llm_model, session=session)
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    msgs = evict_old_tool_results(ctx, list(history))
    cleared = sum(
        1
        for m in msgs
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and is_cleared_marker(p.content)
    )
    assert cleared == N_READ - COMPACTABLE_KEEP_RECENT, (
        f"P1 must clear {N_READ - COMPACTABLE_KEEP_RECENT} old tool results, got {cleared}"
    )

    ctx_window = deps.model.context_window if deps.model else None
    budget = resolve_compaction_budget(deps.config, ctx_window)
    bounds = plan_compaction_boundaries(msgs, budget, deps.config.compaction.tail_fraction)
    assert bounds is not None, (
        f"history must exceed tail budget (budget={budget}, tail={budget * deps.config.compaction.tail_fraction:.0f})"
    )

    len_pre = len(msgs)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result, summary_text = await apply_compaction(ctx, msgs, bounds, announce=False)

    assert len(result) < len_pre, "P5 must reduce message count"
    assert summary_text is not None, "P5 LLM must produce a summary (model is set)"

    marker_count = sum(
        1
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart) and SUMMARY_MARKER_PREFIX in str(p.content)
    )
    assert marker_count == 1, f"exactly 1 summary marker expected, got {marker_count}"

    low = summary_text.lower()
    assert any(kw in low for kw in ("jwt", "session")), "goal must appear in summary"


# ---------------------------------------------------------------------------
# Iterative summary: 3-pass cross-compaction preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(200)
async def test_iterative_summary_3_pass_preservation() -> None:
    """Distinctive token from cycle-1 survives into the cycle-3 in-context marker."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm.factory import build_model

    DISTINCTIVE_TOKEN = "JWT_ROTATION_7779"

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    # num_ctx=8192 → tail_budget=1638 tokens. Each cycle uses two 100-line file reads
    # (~1500 tokens each) so every history exceeds the budget and compaction fires
    # deterministically. The larger budget gives the LLM enough room to carry the
    # distinctive token across iterative summaries.
    cfg = make_settings(llm=make_settings().llm.model_copy(update={"num_ctx": 8192}))
    llm_model = build_model(cfg.llm)
    deps = CoDeps(shell=ShellBackend(), config=cfg, model=llm_model, session=CoSessionState())
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())

    ctx_window = deps.model.context_window if deps.model else None
    budget = resolve_compaction_budget(deps.config, ctx_window)

    # Messages [0-1] become the HEAD (find_first_run_end anchors on the first
    # ModelResponse with TextPart). Anything after that is eligible for dropping.
    # Put DISTINCTIVE_TOKEN in message [3] so it lands in the dropped middle and
    # the summarizer picks it up for cycle-1's compaction marker.
    cycle1 = [
        _user("Implement JWT auth."),
        _assistant(_analysis("auth/tokens.py")),  # head ends here
        _user("What key rotation policy applies?"),
        _assistant(
            f"Per the security review, we must use {DISTINCTIVE_TOKEN} as the rotation "
            f"interval identifier — it is non-negotiable and must be preserved throughout. "
            + _analysis("auth/policy.py")
        ),
        _user("Read views."),
        _tool_call("file_read", {"file_path": "auth/views.py"}, "c1a"),
        _tool_return("file_read", _fake_file("auth/views", 80), "c1a"),
        _assistant(_analysis("auth/views.py")),
        _user("Read middleware."),
        _tool_call("file_read", {"file_path": "auth/middleware.py"}, "c1b"),
        _tool_return("file_read", _fake_file("auth/middleware", 80), "c1b"),
        _assistant(_analysis("auth/middleware.py")),
        _user("Status?"),
        _assistant("Token service and middleware reviewed."),
    ]

    bounds1 = plan_compaction_boundaries(cycle1, budget, deps.config.compaction.tail_fraction)
    assert bounds1 is not None, f"cycle 1 must exceed tail budget (budget={budget})"
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        history1, summary1 = await apply_compaction(ctx, cycle1, bounds1, announce=False)
    assert summary1 is not None, "cycle 1: LLM summarizer must fire (model is set)"

    cycle2 = [
        *history1,
        _user("Write tests."),
        _tool_call("file_read", {"file_path": "tests/test_tokens.py"}, "c2a"),
        _tool_return("file_read", _fake_file("test_tokens", 100), "c2a"),
        _assistant(_analysis("tests/test_tokens.py")),
        _user("Read test helpers."),
        _tool_call("file_read", {"file_path": "tests/conftest.py"}, "c2b"),
        _tool_return("file_read", _fake_file("tests/conftest", 100), "c2b"),
        _assistant(_analysis("tests/conftest.py")),
        _user("Status?"),
        _assistant("Tests written."),
    ]
    bounds2 = plan_compaction_boundaries(cycle2, budget, deps.config.compaction.tail_fraction)
    assert bounds2 is not None, f"cycle 2 must exceed tail budget (budget={budget})"
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        history2, summary2 = await apply_compaction(ctx, cycle2, bounds2, announce=False)
    assert summary2 is not None, "cycle 2: LLM summarizer must fire (model is set)"

    cycle3 = [
        *history2,
        _user("Deploy to staging."),
        _tool_call("file_read", {"file_path": "deploy/config.yaml"}, "c3a"),
        _tool_return("file_read", _fake_file("deploy/config", 100), "c3a"),
        _assistant(_analysis("deploy/config.yaml")),
        _user("Read deploy script."),
        _tool_call("file_read", {"file_path": "deploy/run.sh"}, "c3b"),
        _tool_return("file_read", _fake_file("deploy/run", 100), "c3b"),
        _assistant(_analysis("deploy/run.sh")),
        _user("Final status?"),
        _assistant("JWT migration complete."),
    ]
    bounds3 = plan_compaction_boundaries(cycle3, budget, deps.config.compaction.tail_fraction)
    assert bounds3 is not None, f"cycle 3 must exceed tail budget (budget={budget})"
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        history3, summary3 = await apply_compaction(ctx, cycle3, bounds3, announce=False)
    assert summary3 is not None, "cycle 3: LLM summarizer must fire (model is set)"

    marker3 = next(
        (
            p.content
            for m in history3
            if isinstance(m, ModelRequest)
            for p in m.parts
            if isinstance(p, UserPromptPart) and SUMMARY_MARKER_PREFIX in str(p.content)
        ),
        None,
    )
    assert marker3 is not None, "cycle-3 summary marker must be present in history"
    assert DISTINCTIVE_TOKEN in marker3, (
        f"{DISTINCTIVE_TOKEN} must survive 3 compaction passes (cross-compaction memory broken)"
    )


# ---------------------------------------------------------------------------
# Summarizer prompt quality: verbatim anchor, corrections, error-feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarizer_verbatim_anchor_in_next_step() -> None:
    """## Next Step must contain a ≥20-char verbatim substring from the last 3 messages."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm.factory import build_model

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    dropped = [
        _user("I need to migrate auth from sessions to JWT. Read the current implementation."),
        _tool_call("file_read", {"file_path": "auth/views.py"}, "c1"),
        _tool_return("file_read", "[session middleware code — 80 lines]", "c1"),
        _assistant(
            "I've read auth/views.py. The session middleware handles login at /auth/login."
        ),
        _user("Now edit auth/views.py to add JWT token generation on successful login."),
        _assistant(
            "I'll add a generate_jwt() call after the authenticate() check in the login view."
        ),
    ]
    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        summary = await summarize_messages(deps, dropped)
    assert _has_verbatim_anchor(summary, dropped), (
        "## Next Step must contain a ≥20-char verbatim anchor from the last 3 messages"
    )


@pytest.mark.asyncio
async def test_summarizer_user_correction_captured() -> None:
    """Final user directive (python-jose) must appear in ## Active Task or ## User Corrections."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm.factory import build_model

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    msgs = [
        _user("Implement JWT auth."),
        _assistant("I'll use PyJWT library for token generation."),
        _user("no, use the built-in hmac module instead of PyJWT"),
        _assistant("Switching to hmac. I'll implement sign_token() using hmac.new()."),
        _user("wait, that's not what I wanted — use python-jose, not hmac"),
        _assistant("Understood, switching to python-jose."),
    ]
    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        summary = await summarize_messages(deps, msgs)

    active_task = _extract_section(summary, "Active Task")
    user_corrections = _extract_section(summary, "User Corrections")
    jose_present = (
        "python-jose" in active_task.lower() or "python-jose" in user_corrections.lower()
    )
    hmac_only = "hmac" in active_task.lower() and "python-jose" not in active_task.lower()

    assert not hmac_only, (
        "## Active Task must not state the rejected choice (hmac) without python-jose"
    )
    assert jose_present, "'python-jose' must appear in ## Active Task or ## User Corrections"


@pytest.mark.asyncio
async def test_summarizer_errors_and_fixes_retained() -> None:
    """## Errors & Fixes must contain the test failure and the user-directed correction."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm.factory import build_model

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    msgs = [
        _user("Run the tests."),
        _assistant("Running tests..."),
        _tool_call("run_shell", {"cmd": "pytest"}, "s1"),
        _tool_return(
            "run_shell", "FAILED: test_jwt_auth — AssertionError: token missing 'exp' claim", "s1"
        ),
        _assistant("The test failed. I'll add the exp claim to the token payload."),
        _tool_call("edit_file", {"file_path": "auth/tokens.py"}, "e1"),
        _tool_return("edit_file", "Edited", "e1"),
        _user(
            "still failing — you added exp to the wrong method, it should be in create_token() not refresh_token()"
        ),
        _assistant("You're right. Adding exp to create_token() instead."),
    ]
    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        summary = await summarize_messages(deps, msgs)

    errors_section = _extract_section(summary, "Errors & Fixes")
    low = errors_section.lower()
    assert errors_section, "## Errors & Fixes section must be present"
    assert any(kw in low for kw in ("exp", "test_jwt_auth", "failed")), (
        "## Errors & Fixes must reference the test failure"
    )
    assert "create_token" in low, (
        "## Errors & Fixes must reference the user-directed correction (create_token)"
    )


# ---------------------------------------------------------------------------
# Pending/Resolved sections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarizer_pending_user_asks() -> None:
    """Unanswered question must appear in ## Pending User Asks."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm.factory import build_model

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    msgs = [
        _user("Implement JWT token blacklisting."),
        _assistant("I'll implement the Redis-based token blacklist."),
        _tool_call("file_read", {"file_path": "auth/tokens.py"}, "c1"),
        _tool_return("file_read", _fake_file("auth/tokens", 15), "c1"),
        _assistant("I've read the tokens module. Implementing the blacklist service now."),
        _user("What TTL should we use for blacklisted tokens?"),
        _assistant(
            "I'll continue implementing the service structure. We can decide the TTL later."
        ),
        _tool_call("edit_file", {"file_path": "auth/blacklist.py"}, "c2"),
        _tool_return("edit_file", "Edited", "c2"),
        _assistant("Blacklist service skeleton done. TTL left as a placeholder."),
    ]
    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        summary = await summarize_messages(deps, msgs)

    pending = _extract_section(summary, "Pending User Asks")
    assert pending, "## Pending User Asks must be present for unanswered TTL question"


@pytest.mark.asyncio
async def test_summarizer_resolved_questions() -> None:
    """Explicitly answered question must appear in ## Resolved Questions, not Pending."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm.factory import build_model

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    msgs = [
        _user("Which hashing algorithm should we use for JWT signing?"),
        _assistant(
            "We should use HS256. It is a symmetric HMAC algorithm — simpler to configure than "
            "RS256 since it uses a single shared secret. For an internal service, HS256 is standard."
        ),
        _user("Makes sense. Let's proceed with HS256."),
        _assistant("I'll implement JWT signing with HS256 in the token service now."),
        _tool_call("edit_file", {"file_path": "auth/tokens.py"}, "c3"),
        _tool_return("edit_file", "Edited", "c3"),
        _assistant(
            "JWT signing implemented with HS256. Token payload includes user_id, email, exp claims."
        ),
    ]
    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        summary = await summarize_messages(deps, msgs)

    resolved = _extract_section(summary, "Resolved Questions")
    pending = _extract_section(summary, "Pending User Asks")

    assert resolved or "## Resolved Questions" in summary, (
        "## Resolved Questions must be present for explicitly answered algorithm question"
    )
    algo_in_pending = pending and any(
        kw in pending.lower() for kw in ("hs256", "algorithm", "hashing")
    )
    assert not algo_in_pending, (
        "answered algorithm question must not appear in ## Pending User Asks"
    )


@pytest.mark.asyncio
async def test_summarizer_pending_migrates_to_resolved() -> None:
    """Prior ## Pending item answered in new block must migrate to ## Resolved Questions."""
    from tests._ollama import ensure_ollama_warm

    from co_cli.llm.factory import build_model

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    prior_summary = (
        "## Goal\nImplement JWT authentication with Redis token blacklisting.\n\n"
        "## Key Decisions\nUsing PyJWT with HS256 signing.\n\n"
        "## Working Set\nauth/tokens.py, auth/middleware.py\n\n"
        "## Pending User Asks\nWhat Redis TTL should we use for blacklisted tokens?\n\n"
        "## Next Step\nImplement the Redis token blacklist service."
    )
    dropped = [
        summary_marker(8, prior_summary),
        _user("Use 15 minutes TTL for blacklisted access tokens and 7 days for refresh tokens."),
        _assistant(
            "Setting Redis TTL: 15 minutes (900 seconds) for blacklisted access tokens and "
            "7 days (604800 seconds) for refresh tokens."
        ),
        _tool_call("edit_file", {"file_path": "auth/blacklist.py"}, "c4"),
        _tool_return("edit_file", "Edited", "c4"),
        _assistant(
            "Updated auth/blacklist.py: ACCESS_TOKEN_BLACKLIST_TTL = 900, REFRESH_TOKEN_BLACKLIST_TTL = 604800."
        ),
    ]
    llm_model = build_model(_CONFIG.llm)
    deps = CoDeps(shell=ShellBackend(), config=_CONFIG, model=llm_model)

    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    context = gather_compaction_context(ctx, dropped=dropped)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        summary = await summarize_messages(deps, dropped, context=context)

    resolved = _extract_section(summary, "Resolved Questions")
    pending = _extract_section(summary, "Pending User Asks")

    has_resolved = bool(resolved) or "## Resolved Questions" in summary
    ttl_in_pending = bool(pending) and any(
        kw in pending.lower() for kw in ("ttl", "redis", "blacklist")
    )

    assert has_resolved, "## Resolved Questions must appear after prior pending item is answered"
    assert not ttl_in_pending, "answered TTL question must not remain in ## Pending User Asks"
