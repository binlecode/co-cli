"""Behavioral tests for proactive_window_processor and the circuit breaker gate.

Production path: co_cli/context/compaction.py:proactive_window_processor

Test 1: no LLM — below threshold, returns early.
Test 2: LLM call — above threshold, compaction applied.
Test 3: no LLM — anti-thrash gate fires, falls back to a static-marker compaction.
Tests 4-8: _summarization_gate_open circuit breaker cadence (counts 0-2, 3-12, 13, 14-22, 23).
"""

import asyncio
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
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS

from co_cli.config.tuning import BREAKER_PROBE_EVERY, BREAKER_TRIP, PERSISTED_OUTPUT_TAG
from co_cli.context.compaction import (
    STATIC_MARKER_PREFIX,
    SUMMARY_MARKER_PREFIX,
    TODO_SNAPSHOT_PREFIX,
    _partition_dropped,
    _record_proactive_outcome,
    _resolve_proactive_focus,
    _summarization_gate_open,
    compact_messages,
    is_compaction_marker,
    proactive_window_processor,
    static_marker,
    summary_marker,
)
from co_cli.context.history_processors import spill_largest_tool_results
from co_cli.context.summarization import (
    _build_summarizer_prompt,
    effective_request_tokens,
    serialize_messages,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def _tight_settings():
    """Settings used with model_max_context_tokens=200 → budget=200, token_threshold≈130, tail_budget≈40.

    Each message part uses 160 chars = 40 tokens. A 4-turn history (8 messages)
    totals 320 tokens >> threshold. The head-guard keeps only group[3] in the
    tail (start_index=6 > head_end=2), so plan_compaction_boundaries returns
    non-None and compaction can proceed.
    """
    return SETTINGS_NO_MCP


def _summary_fit_settings():
    """Settings for the success-path tests: a realistic window so the pre-flight fit
    guard passes, with a low trigger ratio so the same small 320-token fixture still
    fires compaction.

    The fit guard requires ``window > assembled_prompt + reserved_cap + safety_margin``
    (the margin alone is 2,000 tokens, so the legacy 200-token window can never admit a
    real summary). Pairing an 8,192 window with ``compaction_ratio=0.03`` keeps the
    threshold (~245) below the fixture's ~320 tokens, so the trigger fires and the
    dropped region stays small — the summary is the same cheap call it always was.
    ``tail_fraction`` is dropped under the ratio to satisfy ``tail_fraction < ratio``.
    """
    base = SETTINGS_NO_MCP
    return base.model_copy(
        update={
            "compaction": base.compaction.model_copy(
                update={"compaction_ratio": 0.03, "tail_fraction": 0.01}
            )
        }
    )


def _above_threshold_messages() -> list:
    """4-turn history well above the 130-token threshold under tight settings."""
    content = "A" * 160  # 160 chars = 40 tokens per part
    return [
        _req(content),
        _resp(content),
        _req(content),
        _resp(content),
        _req(content),
        _resp(content),
        _req(content),
        _resp(content),
    ]


_TIGHT_MODEL = _LLM_MODEL


@pytest.mark.asyncio
async def test_processor_returns_messages_unchanged_when_below_threshold() -> None:
    """Processor returns the same list object when token count is below threshold.

    No LLM call — threshold not crossed, function returns early before the planner.
    Failure mode: below-threshold history gets compacted → context is silently
    shrunk for no reason, corrupting the conversation every turn.
    """
    short_history = [_req("hello"), _resp("hi")]
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
    )
    ctx = RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())

    result = await proactive_window_processor(ctx.deps, short_history)

    assert result is short_history, "Messages must be the same object when below threshold"
    assert deps.runtime.compaction_applied_this_turn is False


@pytest.mark.asyncio
async def test_processor_applies_compaction_when_above_threshold() -> None:
    """Processor compacts and returns a shorter list when token count is above threshold.

    Uses tight budget (max_context_tokens=200, token_threshold≈130) against a 4-turn history
    of ~320 tokens. Makes a real LLM summarizer call.
    Failure mode: above-threshold history is silently passed through → context
    window exhaustion, no proactive compaction ever fires.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_context_tokens=200,
    )
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx.deps, messages)

    assert result is not messages, "Compacted result must be a new list"
    assert len(result) < len(messages), "Compacted result must be shorter than input"
    assert deps.runtime.compaction_applied_this_turn is True
    assert any(
        is_compaction_marker(part.content)
        for msg in result
        for part in getattr(msg, "parts", [])
        if isinstance(getattr(part, "content", None), str)
    ), "Compaction marker must be present in result messages"


@pytest.mark.asyncio
async def test_anti_thrash_gate_falls_back_to_static_marker() -> None:
    """Anti-thrash gate falls back to a static-marker compaction, never a no-op.

    No LLM call — when the gate trips, the summarizer is skipped (summarize=False)
    and the dropped region is replaced by a static marker. This is the
    deterministic, Ollama-free path: no ensure_ollama_warm, no asyncio.timeout.
    The static marker (not a summary marker) is the observable proof that no
    summarization ran, and the closing status string truthfully reports an
    intentional static-marker compaction — never the "Summarizer failed" wording,
    which would be a lie for a deliberate skip where the summarizer never ran.

    Failure mode: gate mis-wired back to the old no-op → text/reasoning context
    grows unbounded toward the hard limit, recovered only after the model errors.
    """
    settings = _tight_settings()
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=settings,
        session=CoSessionState(),
        model_max_context_tokens=200,
    )
    # Trip the anti-thrash gate to exactly the trip threshold.
    deps.runtime.consecutive_low_yield_proactive_compactions = (
        settings.compaction.proactive_thrash_window
    )
    captured: list[str] = []
    deps.runtime.status_callback = captured.append
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    result = await proactive_window_processor(ctx.deps, messages)

    # The bug this fix kills: the gate used to no-op, leaving the history
    # untrimmed so it grew toward the hard limit. A shorter history proves the
    # gate now trims instead of passing the conversation through unchanged.
    assert len(result) < len(messages), "Anti-thrash gate must trim the history, not no-op"

    # A static marker (not a summary marker) in the dropped region's slot is the
    # observable proof that the expensive summarizer never ran on this path.
    marker_contents = [
        part.content
        for msg in result
        for part in getattr(msg, "parts", [])
        if isinstance(getattr(part, "content", None), str)
    ]
    assert any(c.startswith(STATIC_MARKER_PREFIX) for c in marker_contents), (
        "Dropped region must be replaced by a static marker (cheap, no LLM)"
    )
    assert not any(c.startswith(SUMMARY_MARKER_PREFIX) for c in marker_contents), (
        "No summary marker — the summarizer must not run on the anti-thrash path"
    )

    # The user-facing status must reflect an intentional static-marker compaction,
    # not a summarizer failure — the summarizer was skipped by design here.
    assert "Compacted (static marker)." in captured
    assert "Summarizer failed — used static marker." not in captured


# --- Floor-aware trigger tests (ISSUE-1.5) ---


@pytest.mark.asyncio
async def test_floor_aware_trigger_fires_on_static_floor() -> None:
    """Trigger fires on the floor-inclusive realtime size, not the message list alone.

    model_max_context_tokens=700 → threshold=350. The 4-turn history is ~320 message-only tokens (below
    threshold, so the floor-blind local would NOT fire), but the static prefill floor is real
    input. A floor-aware local sees static_floor + 320 > 350 and compacts. Without the floor,
    this history grows uncounted.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_context_tokens=700,
        static_floor_tokens=100,
    )
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx.deps, messages)

    assert result is not messages, "Floor-inclusive size crossed the threshold — must compact"
    assert deps.runtime.compaction_applied_this_turn is True


@pytest.mark.asyncio
async def test_small_realtime_no_compaction_despite_high_provider_usage() -> None:
    """L3 keys off the realtime payload, not a stale-high provider count.

    The trailing ModelResponse reports input_tokens=20_000 (what the removed
    max(.., reported) floor used to read from), but the realtime payload is a
    two-message history far below the 350 threshold. The trigger ignores the
    provider count and does not compact — the L3 analogue of the L2 no-spill case.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_context_tokens=700,
        static_floor_tokens=100,
    )
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = [
        _req("hello"),
        ModelResponse(parts=[TextPart(content="hi")], usage=RequestUsage(input_tokens=20_000)),
    ]

    result = await proactive_window_processor(ctx.deps, messages)

    assert result is messages, (
        "Small realtime payload must not compact despite high provider usage"
    )
    assert deps.runtime.compaction_applied_this_turn is False


def test_savings_uses_floor_inclusive_basis() -> None:
    """Low-yield pass increments the thrash counter when savings use a floor-inclusive basis.

    The trigger's token_count is the floor-inclusive realtime estimate. Computing savings against a
    floor-EXCLUDED tokens_after would overstate yield and falsely reset the counter on a genuinely
    low-yield pass. With both sides floor-inclusive, savings = (1000 - (500+450))/1000 = 5% < the
    10% min, so the counter correctly increments.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_tight_settings(),
        session=CoSessionState(),
        static_floor_tokens=500,
    )
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    # result estimate = 1800 chars // 4 = 450 tokens; tokens_after = 500 + 450 = 950.
    result = [_resp("x" * 1800)]

    _record_proactive_outcome(ctx.deps, [_req("q")], result, "a summary", token_count=1000)

    assert deps.runtime.consecutive_low_yield_proactive_compactions == 1, (
        "Floor-inclusive savings (5%) is below the 10% min — counter must increment"
    )


def test_status_estimate_written_back_after_proactive() -> None:
    """After a proactive pass, runtime.current_request_tokens_estimate is the compacted size.

    The status line reads current_request_tokens_estimate; an L3 pass must overwrite
    the stale pre-compaction (L2 spill) estimate with the post-compaction count, else
    the status line keeps reporting the larger pre-compaction size.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_tight_settings(),
        session=CoSessionState(),
        static_floor_tokens=500,
    )
    # Seed a stale (pre-compaction) estimate to prove the write-back overwrites it.
    deps.runtime.current_request_tokens_estimate = 999_999
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    result = [_resp("x" * 1800)]

    returned = _record_proactive_outcome(
        ctx.deps, [_req("q")], result, "a summary", token_count=1000
    )

    expected = effective_request_tokens(deps, result)
    assert deps.runtime.current_request_tokens_estimate == expected, (
        "current_request_tokens_estimate must equal the post-compaction effective size"
    )
    assert returned == expected, "_record_proactive_outcome must return tokens_after"


def _no_progress_messages() -> list[ModelMessage]:
    """A transcript where the applied static-marker pass cannot shrink the payload.

    Head pins [u0, r0] (first run). The only droppable middle is a 1-char user
    message; the huge final turn group is retained unconditionally. Replacing the
    tiny middle with a (larger) static marker means tokens_after >= token_count —
    a genuine no-progress pass, not an engineered counter.
    """
    return [
        _req("u"),
        _resp("r"),
        _req("m"),
        _req("X" * 2000),
    ]


@pytest.mark.asyncio
async def test_no_progress_escalates_to_recovery_once(monkeypatch) -> None:
    """A no-progress applied pass escalates to recover_overflow_history exactly once.

    done_when (plan TASK-5): with a spy on recover_overflow_history, a transcript
    whose retained last turn group alone exceeds the tail budget calls it exactly
    once and returns its output — not a re-fired identical no-op proactive pass.
    model=None keeps the summarizer gated off, so the path is deterministic.
    """
    calls: list[list[ModelMessage]] = []
    sentinel: list[ModelMessage] = [_req("recovered")]

    async def spy(ctx_, messages_):
        calls.append(messages_)
        return sentinel

    monkeypatch.setattr("co_cli.context.compaction.recover_overflow_history", spy)

    deps = CoDeps(
        shell=ShellBackend(),
        model=None,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_context_tokens=200,
    )
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    messages = _no_progress_messages()

    result = await proactive_window_processor(ctx.deps, messages)

    assert len(calls) == 1, "no-progress pass must escalate to recovery exactly once"
    assert result is sentinel, "processor must return the recovery output"


@pytest.mark.asyncio
async def test_no_progress_recovery_none_is_fail_open(monkeypatch) -> None:
    """When escalated recovery returns None, the processor returns messages unchanged.

    done_when (plan TASK-5): a None recovery leaves messages unchanged (fail-open).
    """

    async def none_recovery(ctx_, messages_):
        return None

    monkeypatch.setattr("co_cli.context.compaction.recover_overflow_history", none_recovery)

    deps = CoDeps(
        shell=ShellBackend(),
        model=None,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_context_tokens=200,
    )
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    messages = _no_progress_messages()

    result = await proactive_window_processor(ctx.deps, messages)

    assert result is messages, "fail-open: a None recovery must return the original messages"


# --- Circuit breaker gate tests ---


def _gate_ctx(skip_count: int) -> tuple[RunContext[CoDeps], CoDeps]:
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
    )
    deps.runtime.compaction_skip_count = skip_count
    ctx = RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())
    return ctx, deps


@pytest.mark.parametrize("count", [2])
def test_gate_open_before_trip(count: int) -> None:
    """Gate is open at the boundary value just below trip (skip_count=2).

    Deletion regression: would not detect the breaker firing before 3 consecutive
    failures, blocking the LLM prematurely.
    """
    ctx, _ = _gate_ctx(count)
    gate_open, _ = _summarization_gate_open(ctx.deps)
    assert gate_open is True


@pytest.mark.parametrize(
    "count",
    [
        BREAKER_TRIP,
        BREAKER_TRIP + BREAKER_PROBE_EVERY - 1,
    ],
)
def test_gate_closed_after_trip(count: int) -> None:
    """Gate is closed at the trip boundary (3) and the last skip before first probe (12).

    Deletion regression: would not detect the breaker staying open after trip,
    making every subsequent call a live LLM probe (circuit breaker never engages).
    """
    ctx, _ = _gate_ctx(count)
    gate_open, _ = _summarization_gate_open(ctx.deps)
    assert gate_open is False


def test_gate_open_at_first_probe() -> None:
    """Gate opens at skip_count 13 (TRIP + PROBE_EVERY) — first LLM probe fires.

    Deletion regression: would not detect permanent blocking (probe window
    silently skipped), leaving the LLM permanently bypassed after any 3 failures.
    """
    ctx, _ = _gate_ctx(BREAKER_TRIP + BREAKER_PROBE_EVERY)
    gate_open, _ = _summarization_gate_open(ctx.deps)
    assert gate_open is True


@pytest.mark.parametrize(
    "count",
    [
        BREAKER_TRIP + BREAKER_PROBE_EVERY + 1,
        BREAKER_TRIP + 2 * BREAKER_PROBE_EVERY - 1,
    ],
)
def test_gate_closed_between_probes(count: int) -> None:
    """Gate is closed at first-skip-after-probe (14) and last-skip-before-second-probe (22).

    Deletion regression: would not detect the gate staying open after a probe,
    making every call post-probe a live LLM attempt (probe cadence lost).
    """
    ctx, _ = _gate_ctx(count)
    gate_open, _ = _summarization_gate_open(ctx.deps)
    assert gate_open is False


def test_gate_open_at_second_probe() -> None:
    """Gate opens at skip_count 23 (TRIP + 2*PROBE_EVERY) -- second probe fires.

    Deletion regression: would not detect the probe cadence stopping after one
    cycle, leaving the LLM permanently blocked after the first probe fails.
    """
    ctx, _ = _gate_ctx(BREAKER_TRIP + 2 * BREAKER_PROBE_EVERY)
    gate_open, _ = _summarization_gate_open(ctx.deps)
    assert gate_open is True


@pytest.mark.asyncio
async def test_successful_compaction_resets_skip_count() -> None:
    """compaction_skip_count resets to 0 after a successful (non-empty) LLM summary.

    Sets skip_count=2 (below trip, gate open) before compaction so the counter is
    non-zero going in. A valid summary must reset it to 0.

    Deletion regression: would not detect a future change that leaves the counter
    non-zero after a successful compaction, silently degrading circuit breaker
    accuracy.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_summary_fit_settings(),
        session=CoSessionState(),
        model_max_context_tokens=8192,
    )
    # Below trip threshold (< 3) so the gate remains open for the LLM call.
    deps.runtime.compaction_skip_count = 2
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx.deps, messages)

    assert result is not messages
    assert deps.runtime.compaction_skip_count == 0, (
        "skip_count must reset to 0 after a successful summarization"
    )


@pytest.mark.asyncio
async def test_l3_fastpaths_after_l2_spill_fits_payload(tmp_path: Path) -> None:
    """Chain: a successful L2 spill deterministically suppresses an L3 summarize.

    This is the behavioral heart of dropping ``reported`` from the trigger. Before
    the spill the realtime payload is well over the summarize threshold; L2 spills
    the oversized tool return to disk, dropping the realtime payload below threshold.
    L3 then re-reads the *same* lowered realtime count and fast-paths
    (below_threshold) — running zero summarizer LLM calls — even though the prior
    ModelResponse reports a high provider input count (the stale-high signal the
    removed ``max(.., reported)`` floor used to read). No provider-reported floor
    keeps L3 firing after the spill already fit the payload.

    model_max_context_tokens=4000 → L3 threshold=2000; spill_threshold=2000. One ~10k-token
    tool return spills to a ~400-token persisted-output stub, landing under both.
    """
    big_content = "data: " + "y" * 40_000
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="run")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="shell_exec", args={}, tool_call_id="tc1")],
            usage=RequestUsage(input_tokens=20_000),
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="shell_exec", content=big_content, tool_call_id="tc1")]
        ),
    ]
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_context_tokens=4000,
        spill_threshold_tokens=2000,
        tool_results_dir=tmp_path,
    )
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())

    after_spill = spill_largest_tool_results(ctx.deps, messages)
    spilled = [
        part.content
        for msg in after_spill
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and isinstance(part.content, str)
    ]
    assert any(c.startswith(PERSISTED_OUTPUT_TAG) for c in spilled), (
        "L2 must spill the oversized return to disk"
    )

    result = await proactive_window_processor(ctx.deps, after_spill)

    assert result is after_spill, (
        "L3 must fast-path (below_threshold) after the spill fit the payload — no summarize"
    )
    assert deps.runtime.compaction_applied_this_turn is False, (
        "a fitting spill must not trigger a summarizer LLM call"
    )


# --- Closing status callback tests ---


@pytest.mark.asyncio
async def test_closing_callback_fires_compacted_on_success() -> None:
    """Closing status callback must fire 'Compacted.' after a successful LLM summarization.

    Deletion regression: would not detect a future change that silences the closing
    callback, leaving 'Compacting conversation...' as the stale final status.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_summary_fit_settings(),
        session=CoSessionState(),
        model_max_context_tokens=8192,
    )
    captured: list[str] = []
    deps.runtime.status_callback = captured.append
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx.deps, messages)

    assert result is not messages
    assert "Compacted." in captured


@pytest.mark.asyncio
async def test_closing_callback_fires_unavailable_when_no_model() -> None:
    """Closing status callback must fire 'LLM compaction unavailable...' when model is absent.

    Deletion regression: would not detect a future change that leaves the no-model
    path silent, giving no feedback when a static marker is silently inserted.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=None,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_context_tokens=200,
    )
    captured: list[str] = []
    deps.runtime.status_callback = captured.append
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    messages = _above_threshold_messages()

    result = await proactive_window_processor(ctx.deps, messages)

    assert result is not messages
    assert "LLM compaction unavailable — used static marker." in captured


@pytest.mark.asyncio
async def test_closing_callback_fires_failed_when_breaker_tripped() -> None:
    """Closing status callback must fire 'Summarizer failed...' when the circuit breaker is tripped.

    Deletion regression: would not detect a future change that silences the
    callback on the breaker path, hiding that a static marker was used.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_context_tokens=200,
    )
    deps.runtime.compaction_skip_count = BREAKER_TRIP
    captured: list[str] = []
    deps.runtime.status_callback = captured.append
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    result = await proactive_window_processor(ctx.deps, messages)

    assert result is not messages
    assert "Summarizer failed — used static marker." in captured


def test_focus_from_in_progress_todo() -> None:
    """_resolve_proactive_focus returns in-progress todo content head-capped at 200 chars.

    Failure mode: focus resolver ignores todos → proactive compaction never uses the
    active task as the summarization anchor, losing on-task signal on every fire.
    """
    long_content = "X" * 300
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(
            session_todos=[
                {"id": "1", "content": long_content, "status": "in_progress", "priority": "high"}
            ]
        ),
    )
    ctx = RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())

    result = _resolve_proactive_focus(ctx.deps, [])

    assert result == long_content[:200]


def test_focus_from_last_user_message() -> None:
    """_resolve_proactive_focus returns most-recent UserPromptPart content tail-capped at 200 chars.

    Failure mode: resolver scans the wrong direction or skips the wrapping ModelRequest,
    returning None when a user message is present — focus is silently lost.
    """
    long_content = "Y" * 300
    messages = [
        ModelRequest(parts=[UserPromptPart(content="first message")]),
        ModelRequest(parts=[UserPromptPart(content=long_content)]),
    ]
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
    )
    ctx = RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())

    result = _resolve_proactive_focus(ctx.deps, messages)

    assert result == long_content[-200:]


def test_focus_none_when_no_todo_and_no_messages() -> None:
    """_resolve_proactive_focus returns None when there are no todos and no messages.

    Failure mode: returns an empty string or raises — both would corrupt the
    focus=None fallthrough path that today's behavior depends on.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
    )
    ctx = RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())

    result = _resolve_proactive_focus(ctx.deps, [])

    assert result is None


def test_focus_skips_compaction_marker() -> None:
    """Focus falls through an inserted compaction marker to the latest real user message.

    Failure mode: a compaction marker is the most-recent UserPromptPart, so focus
    anchors on marker boilerplate instead of the user's task — every proactive
    summary after the first compaction loses its on-task anchor.
    """
    real = "Z" * 300
    messages = [
        ModelRequest(parts=[UserPromptPart(content=real)]),
        static_marker(3),
    ]
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
    )
    ctx = RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())

    result = _resolve_proactive_focus(ctx.deps, messages)

    assert result == real[-200:]


def test_focus_skips_todo_snapshot() -> None:
    """Focus falls through a todo-snapshot marker (not matched by is_compaction_marker).

    The todo snapshot is NOT a compaction marker, so is_compaction_marker alone
    would not skip it — TODO_SNAPSHOT_PREFIX must be tested explicitly. Failure
    mode: focus anchors on the snapshot boilerplate during a pre-input thrash.
    """
    real = "W" * 300
    messages = [
        ModelRequest(parts=[UserPromptPart(content=real)]),
        ModelRequest(
            parts=[UserPromptPart(content=f"{TODO_SNAPSHOT_PREFIX}\n- [ ] do the thing")]
        ),
    ]
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
    )
    ctx = RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())

    result = _resolve_proactive_focus(ctx.deps, messages)

    assert result == real[-200:]


@pytest.mark.asyncio
async def test_prior_summary_partitioned_into_dedicated_slot() -> None:
    """Prior summary markers are lifted out of the turns block into a dedicated slot.

    Deterministic, no LLM: exercises the partition (_partition_dropped), the
    serialized turns (serialize_messages), the gated carry-forward clause
    (_build_summarizer_prompt), and the assembled output shape (compact_messages
    with summarize=False, the no-LLM static path).

    Failure mode (pre-fix): the prior summary marker is rendered inline inside the
    opaque TURNS TO SUMMARIZE: block, where the system prompt's ignore-commands
    rule collides with the task prompt's integrate-this-summary rule — the
    carry-forward silently degrades across long sessions.
    """
    recap = "## Active Task\nUser asked: 'wire up JWT auth'\n\n## Next Step\nimplement login view"
    head = _req("HEAD user turn")
    prior = summary_marker(4, recap, has_tail=True)
    real_req = _req("real user turn in the dropped middle")
    real_resp = _resp("real assistant turn in the dropped middle")
    mid_static = static_marker(2)
    tail = _req("TAIL user turn")
    messages = [head, prior, real_req, real_resp, mid_static, tail]

    dropped = messages[1:5]
    body, prior_summary = _partition_dropped(dropped)

    # (b) the recap is recovered into a dedicated prior_summary value
    assert prior_summary == recap

    # (a)(c) the body fed to the summarizer carries neither a summary nor a static marker
    serialized = serialize_messages(body, [])
    assert SUMMARY_MARKER_PREFIX not in serialized
    assert STATIC_MARKER_PREFIX not in serialized
    # the recap text reaches only the slot, never the opaque turns block
    assert recap not in serialized
    # real turns survive into the body
    assert "real user turn in the dropped middle" in serialized
    assert "real assistant turn in the dropped middle" in serialized

    # (b) the slot-referencing carry-forward clause appears iff a prior summary is present
    with_clause = _build_summarizer_prompt(False, 2000, None, prior_summary)
    without_clause = _build_summarizer_prompt(False, 2000, None, None)
    assert "PRIOR SUMMARY block above" in with_clause
    assert "PRIOR SUMMARY block above" not in without_clause

    # (d) assembled output preserves head / tail / todo-snapshot and carries one fresh marker
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(
            session_todos=[{"id": "1", "content": "ship it", "status": "in_progress"}]
        ),
    )
    ctx = RunContext(deps=deps, model=_LLM_MODEL.model, usage=RunUsage())
    result, summary_text, _ = await compact_messages(
        ctx.deps, messages, (1, 5, 4), summarize=False
    )

    assert summary_text is None
    assert result[0] is head
    assert result[-1] is tail
    marker_count = sum(
        1
        for m in result
        for p in getattr(m, "parts", [])
        if is_compaction_marker(getattr(p, "content", None))
    )
    assert marker_count == 1
    assert any(
        getattr(p, "content", "").startswith(TODO_SNAPSHOT_PREFIX)
        for m in result
        for p in getattr(m, "parts", [])
    )
