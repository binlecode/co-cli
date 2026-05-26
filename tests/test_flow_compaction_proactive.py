"""Behavioral tests for proactive_window_processor and the circuit breaker gate.

Production path: co_cli/context/compaction.py:proactive_window_processor

Test 1: no LLM — below threshold, returns early.
Test 2: LLM call — above threshold, compaction applied.
Test 3: no LLM — anti-thrash gate fires, returns early.
Tests 4-8: _summarization_gate_open circuit breaker cadence (counts 0-2, 3-12, 13, 14-22, 23).
"""

import asyncio

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage, RunUsage
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS

from co_cli.context.compaction import (
    _COMPACTION_BREAKER_PROBE_EVERY,
    _COMPACTION_BREAKER_TRIP,
    _resolve_proactive_focus,
    _summarization_gate_open,
    is_compaction_marker,
    proactive_window_processor,
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
    """Settings used with model_max_ctx=200 → budget=200, token_threshold≈130, tail_budget≈40.

    Each message part uses 160 chars = 40 tokens. A 4-turn history (8 messages)
    totals 320 tokens >> threshold. The head-guard keeps only group[3] in the
    tail (start_index=6 > head_end=2), so plan_compaction_boundaries returns
    non-None and compaction can proceed.
    """
    return SETTINGS_NO_MCP


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

    result = await proactive_window_processor(ctx, short_history)

    assert result is short_history, "Messages must be the same object when below threshold"
    assert deps.runtime.compaction_applied_this_turn is False


@pytest.mark.asyncio
async def test_processor_applies_compaction_when_above_threshold() -> None:
    """Processor compacts and returns a shorter list when token count is above threshold.

    Uses tight budget (max_ctx=200, token_threshold≈130) against a 4-turn history
    of ~320 tokens. Makes a real LLM summarizer call.
    Failure mode: above-threshold history is silently passed through → context
    window exhaustion, no proactive compaction ever fires.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_ctx=200,
    )
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx, messages)

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
async def test_anti_thrash_gate_skips_compaction_after_consecutive_low_yield() -> None:
    """Processor returns messages unchanged when the anti-thrash gate is active.

    No LLM call — gate fires before the planner is reached.
    Failure mode: gate mis-wired → even when thrash_window is exceeded, the
    summarizer still fires, burning context budget on low-yield passes.
    """
    settings = _tight_settings()
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=settings,
        session=CoSessionState(),
        model_max_ctx=200,
    )
    # Trip the anti-thrash gate to exactly the trip threshold
    deps.runtime.consecutive_low_yield_proactive_compactions = (
        settings.compaction.proactive_thrash_window
    )
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    result = await proactive_window_processor(ctx, messages)

    assert result is messages, "Gate must return original messages unchanged"
    assert deps.runtime.compaction_applied_this_turn is False


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
    gate_open, _ = _summarization_gate_open(ctx)
    assert gate_open is True


@pytest.mark.parametrize(
    "count",
    [
        _COMPACTION_BREAKER_TRIP,
        _COMPACTION_BREAKER_TRIP + _COMPACTION_BREAKER_PROBE_EVERY - 1,
    ],
)
def test_gate_closed_after_trip(count: int) -> None:
    """Gate is closed at the trip boundary (3) and the last skip before first probe (12).

    Deletion regression: would not detect the breaker staying open after trip,
    making every subsequent call a live LLM probe (circuit breaker never engages).
    """
    ctx, _ = _gate_ctx(count)
    gate_open, _ = _summarization_gate_open(ctx)
    assert gate_open is False


def test_gate_open_at_first_probe() -> None:
    """Gate opens at skip_count 13 (TRIP + PROBE_EVERY) — first LLM probe fires.

    Deletion regression: would not detect permanent blocking (probe window
    silently skipped), leaving the LLM permanently bypassed after any 3 failures.
    """
    ctx, _ = _gate_ctx(_COMPACTION_BREAKER_TRIP + _COMPACTION_BREAKER_PROBE_EVERY)
    gate_open, _ = _summarization_gate_open(ctx)
    assert gate_open is True


@pytest.mark.parametrize(
    "count",
    [
        _COMPACTION_BREAKER_TRIP + _COMPACTION_BREAKER_PROBE_EVERY + 1,
        _COMPACTION_BREAKER_TRIP + 2 * _COMPACTION_BREAKER_PROBE_EVERY - 1,
    ],
)
def test_gate_closed_between_probes(count: int) -> None:
    """Gate is closed at first-skip-after-probe (14) and last-skip-before-second-probe (22).

    Deletion regression: would not detect the gate staying open after a probe,
    making every call post-probe a live LLM attempt (probe cadence lost).
    """
    ctx, _ = _gate_ctx(count)
    gate_open, _ = _summarization_gate_open(ctx)
    assert gate_open is False


def test_gate_open_at_second_probe() -> None:
    """Gate opens at skip_count 23 (TRIP + 2*PROBE_EVERY) -- second probe fires.

    Deletion regression: would not detect the probe cadence stopping after one
    cycle, leaving the LLM permanently blocked after the first probe fails.
    """
    ctx, _ = _gate_ctx(_COMPACTION_BREAKER_TRIP + 2 * _COMPACTION_BREAKER_PROBE_EVERY)
    gate_open, _ = _summarization_gate_open(ctx)
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
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_ctx=200,
    )
    # Below trip threshold (< 3) so the gate remains open for the LLM call.
    deps.runtime.compaction_skip_count = 2
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx, messages)

    assert result is not messages
    assert deps.runtime.compaction_skip_count == 0, (
        "skip_count must reset to 0 after a successful summarization"
    )


@pytest.mark.asyncio
async def test_thrash_counter_not_incremented_for_reported_driven_compaction() -> None:
    """Reported-dominant trigger must not produce a false low-yield event.

    When provider-reported input_tokens >> local char-based estimate, the
    compaction marker preamble overhead causes local_after > local_before
    even when the LLM produces a concise summary. With the old savings formula
    (local_before as denominator), savings would be negative, tripping the thrash
    counter for a legitimate compaction. The fix uses max(local, reported) as
    the effective-before, so effective savings = (reported - local_after) / reported
    which is clearly positive whenever local_after < reported.

    Failure mode: two consecutive reported-driven compactions trip the anti-thrash
    gate (proactive_thrash_window=2) and silence proactive compaction for the session.
    """
    settings = _tight_settings()
    deps = CoDeps(
        shell=ShellBackend(),
        model=_TIGHT_MODEL,
        config=settings,
        session=CoSessionState(),
        model_max_ctx=200,
    )
    # Simulate what TokenTrackingCapability.after_model_request would have written
    # from the trailing ModelResponse's usage. The proactive processor reads this
    # field, not the scan of message.usage values.
    deps.runtime.last_reported_input_tokens = 2000
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())

    # 4-turn meaningful conversation — local estimate ~119 tokens, just above threshold
    # of 100 (model_max_ctx=200, compaction_ratio=0.50). last_reported_input_tokens=2000
    # (reported >> local), so token_count = max(119, 2000) = 2000.
    #
    # After compaction the marker preamble overhead pushes local_after > local_before
    # (~522 tokens for the result vs ~119 for the input), making local savings negative.
    # OLD savings formula: (119 - 522) / 119 ≈ -338%  → counter increments (bug).
    # NEW savings formula: (2000 - 522) / 2000 ≈  74%  → counter stays at 0 (fixed).
    messages = [
        _req("Tell me about machine learning."),
        _resp("Machine learning is a branch of AI that enables systems to learn from data."),
        _req("What algorithms are commonly used?"),
        _resp("Common algorithms include linear regression, decision trees, and neural networks."),
        _req("How does gradient descent work?"),
        _resp(
            "Gradient descent minimizes loss by iteratively updating parameters "
            "in the direction of the negative gradient."
        ),
        _req("What is overfitting?"),
        ModelResponse(
            parts=[
                TextPart(
                    content="Overfitting occurs when a model learns training data "
                    "too well and fails to generalize to new data."
                )
            ],
            usage=RequestUsage(input_tokens=2000),
        ),
    ]

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx, messages)

    assert result is not messages, "Compaction must have fired"
    assert deps.runtime.compaction_applied_this_turn is True
    assert deps.runtime.consecutive_low_yield_proactive_compactions == 0, (
        "Reported-driven compaction must not increment the thrash counter"
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
        config=_tight_settings(),
        session=CoSessionState(),
        model_max_ctx=200,
    )
    captured: list[str] = []
    deps.runtime.status_callback = captured.append
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await proactive_window_processor(ctx, messages)

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
        model_max_ctx=200,
    )
    captured: list[str] = []
    deps.runtime.status_callback = captured.append
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())
    messages = _above_threshold_messages()

    result = await proactive_window_processor(ctx, messages)

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
        model_max_ctx=200,
    )
    deps.runtime.compaction_skip_count = _COMPACTION_BREAKER_TRIP
    captured: list[str] = []
    deps.runtime.status_callback = captured.append
    ctx = RunContext(deps=deps, model=_TIGHT_MODEL.model, usage=RunUsage())
    messages = _above_threshold_messages()

    result = await proactive_window_processor(ctx, messages)

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

    result = _resolve_proactive_focus(ctx, [])

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

    result = _resolve_proactive_focus(ctx, messages)

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

    result = _resolve_proactive_focus(ctx, [])

    assert result is None
