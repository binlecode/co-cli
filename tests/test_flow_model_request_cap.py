"""Tests for model-request cap and tool-call-cap hard-stop in run_turn().

Production paths:
  co_cli/config/llm.py              — LlmSettings.max_model_requests_per_turn, resolve_request_limit
  co_cli/agent/orchestrate.py       — _check_turn_caps, run_turn
  co_cli/context/history_processors.py — wrap_up_on_final_request, drop_wrap_up_messages
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.orchestrate import run_turn
from co_cli.agent.toolset import _CallSeamToolset
from co_cli.config.core import load_config
from co_cli.config.llm import MAX_MODEL_REQUESTS_PER_TURN, LlmSettings
from co_cli.config.tuning import MAX_TOOL_CALLS_PER_MODEL_REQUEST
from co_cli.context.history_processors import WRAP_UP_TEXT, wrap_up_on_final_request
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# Unit: LlmSettings defaults and env var override
# ---------------------------------------------------------------------------


def test_max_model_requests_default_is_40() -> None:
    """LlmSettings() with no overrides yields max_model_requests_per_turn == 40."""
    s = LlmSettings(provider="ollama")
    assert s.max_model_requests_per_turn == MAX_MODEL_REQUESTS_PER_TURN
    assert s.max_model_requests_per_turn == 40


def test_max_model_requests_env_override(tmp_path: Path) -> None:
    """CO_LLM_MAX_MODEL_REQUESTS_PER_TURN=7 overrides the default to 7."""
    result = load_config(
        _user_config_path=tmp_path / "settings.json",
        _env={"CO_LLM_MAX_MODEL_REQUESTS_PER_TURN": "7"},
    )
    assert result.llm.max_model_requests_per_turn == 7


def test_max_model_requests_env_zero_disables_cap(tmp_path: Path) -> None:
    """CO_LLM_MAX_MODEL_REQUESTS_PER_TURN=0 sets cap to 0 (disabled)."""
    result = load_config(
        _user_config_path=tmp_path / "settings.json",
        _env={"CO_LLM_MAX_MODEL_REQUESTS_PER_TURN": "0"},
    )
    assert result.llm.max_model_requests_per_turn == 0


# ---------------------------------------------------------------------------
# Helpers shared by integration tests
# ---------------------------------------------------------------------------


def _make_capped_deps(max_model_requests: int) -> CoDeps:
    """Return CoDeps with max_model_requests_per_turn pinned."""
    config = SETTINGS_NO_MCP.model_copy(
        update={
            "llm": SETTINGS_NO_MCP.llm.model_copy(
                update={"max_model_requests_per_turn": max_model_requests}
            )
        }
    )
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        session=CoSessionState(),
        model_max_context_tokens=config.llm.max_context_tokens,
    )


# ---------------------------------------------------------------------------
# Integration (b): model-request cap via run_turn
#
# Strategy: build a stub agent with one approval-required tool. The model
# returns that tool call on every call, driving approval-resume runs that
# accumulate model requests across the turn. With max_model_requests_per_turn=2,
# the SDK request cap fires mid-turn before the 3rd request (before-request
# semantics: the request that would exceed the cap is blocked, not the one that
# reaches it).
# ---------------------------------------------------------------------------


def _make_model_request_cap_agent() -> Agent:
    """Agent that issues an approval-required tool call on every model request.

    Protocol:
      model call 1 — yield approval-required tool call (initial run)
      model call 2 — yield approval-required tool call (resume run 1)
      model call 3+ — would yield another approval call, but with the cap pinned
                      at 2 the SDK blocks the 3rd request before it is sent.
    """
    call_count = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        if n <= 4:
            yield {0: DeltaToolCall(name="needs_approval", json_args="{}", tool_call_id=f"c{n}")}
        else:
            yield "done"

    toolset: FunctionToolset = FunctionToolset()

    async def needs_approval(ctx: RunContext[CoDeps]) -> str:
        return "approved"

    toolset.add_function(needs_approval, requires_approval=True)

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_CallSeamToolset(toolset)],
    )


@pytest.mark.asyncio
async def test_model_request_cap_fires_after_approval_loop() -> None:
    """run_turn with max_model_requests_per_turn=2 must stop before the 3rd request.

    The stub model returns approval-required tool calls, driving approval-resume
    runs that accumulate requests across the turn. After requests 1 and 2 complete,
    the SDK blocks request 3 (before-request semantics) and the turn errors.
    """
    deps = _make_capped_deps(max_model_requests=2)
    agent = _make_model_request_cap_agent()
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="ping",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "error", f"expected error outcome; got {turn.outcome!r}"
    assert any("Model-request cap" in s for s in frontend.statuses), (
        f"status must mention 'Model-request cap'; got statuses: {frontend.statuses}"
    )
    assert turn.model_requests == 2, (
        f"reported requests must equal the cap; got {turn.model_requests}"
    )


# ---------------------------------------------------------------------------
# Integration (c): tool-call-cap hard-stop via run_turn
#
# Strategy: one approval-required tool (initial run), then 3 rounds of
# MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1 noop calls in the resume run.
# Each over-cap round increments the streak in the routing wrapper.  After 3
# consecutive violations, _run_approval_loop sets tool_cap_hard_stop and breaks.
# ---------------------------------------------------------------------------


def _make_hard_stop_agent() -> Agent:
    """Agent that causes 3 consecutive tool-cap violations inside the approval loop.

    Protocol:
      model call 1 — approval-required call (initial run → DeferredToolRequests)
      model calls 2-4 — each streams MAX_TOOL_CALLS_PER_MODEL_REQUEST+1 noop calls
                        (one over cap → violation each round)
      model call 5+  — stream text "done"

    After model call 4's tools execute, consecutive_tool_cap_violations == 3 and
    _run_approval_loop fires the hard-stop.
    """
    call_count = {"n": 0}
    _OVER = MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        if n == 1:
            yield {0: DeltaToolCall(name="needs_approval", json_args="{}", tool_call_id="c0")}
        elif 2 <= n <= 4:
            for i in range(_OVER):
                yield {
                    i: DeltaToolCall(
                        name="noop", json_args=f'{{"x":{i}}}', tool_call_id=f"c{n}x{i}"
                    )
                }
        else:
            yield "done"

    toolset: FunctionToolset = FunctionToolset()

    async def needs_approval(ctx: RunContext[CoDeps]) -> str:
        return "approved"

    async def noop(ctx: RunContext[CoDeps], x: int) -> str:
        return f"noop {x}"

    toolset.add_function(needs_approval, requires_approval=True)
    toolset.add_function(noop, requires_approval=False)

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_CallSeamToolset(toolset)],
    )


@pytest.mark.asyncio
async def test_hard_stop_surfaces_final_answer_after_consecutive_violations() -> None:
    """After 3 consecutive violations the hard-stop must surface the model's final answer.

    Flow:
      initial run — model streams approval-required tool → DeferredToolRequests
      resume run  — 3 rounds of MAX_TOOL_CALLS_PER_MODEL_REQUEST+1 noop calls trigger
                        3 consecutive tool-cap violations → _run_approval_loop hard-stops,
                        then the model emits final text "done"
      _check_turn_caps → outcome='continue', output 'done', cap status still emitted
    """
    deps = _make_capped_deps(max_model_requests=90)
    agent = _make_hard_stop_agent()
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="trigger hard stop",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "continue", f"expected surfaced answer; got {turn.outcome!r}"
    assert turn.output == "done"
    assert any("Tool-call cap exceeded" in s for s in frontend.statuses), (
        f"status must mention 'Tool-call cap exceeded'; got statuses: {frontend.statuses}"
    )


def _make_over_then_under_cap_agent() -> Agent:
    """Agent: one over-cap request followed by an under-cap request, then text.

    Protocol:
      model call 1 — approval-required call (initial run → DeferredToolRequests)
      model call 2 — MAX_TOOL_CALLS_PER_MODEL_REQUEST+1 noop calls (one over cap)
      model call 3 — exactly 1 noop call (under cap → request behaves)
      model call 4 — text "done"

    The under-cap final tool request must reset the streak at the run boundary,
    so the hard-stop never fires and the turn completes normally.
    """
    call_count = {"n": 0}
    _OVER = MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        if n == 1:
            yield {0: DeltaToolCall(name="needs_approval", json_args="{}", tool_call_id="c0")}
        elif n == 2:
            for i in range(_OVER):
                yield {
                    i: DeltaToolCall(name="noop", json_args=f'{{"x":{i}}}', tool_call_id=f"a{i}")
                }
        elif n == 3:
            yield {0: DeltaToolCall(name="noop", json_args='{"x":0}', tool_call_id="b0")}
        else:
            yield "done"

    toolset: FunctionToolset = FunctionToolset()

    async def needs_approval(ctx: RunContext[CoDeps]) -> str:
        return "approved"

    async def noop(ctx: RunContext[CoDeps], x: int) -> str:
        return f"noop {x}"

    toolset.add_function(needs_approval, requires_approval=True)
    toolset.add_function(noop, requires_approval=False)

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_CallSeamToolset(toolset)],
    )


@pytest.mark.asyncio
async def test_under_cap_request_after_over_cap_does_not_hard_stop() -> None:
    """A single over-cap request followed by an under-cap one must NOT hard-stop.

    Proves per-request granularity: the streak is finalized to 0 at the run
    boundary because the last tool-issuing request stayed within the cap.
    """
    deps = _make_capped_deps(max_model_requests=90)
    agent = _make_over_then_under_cap_agent()
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="over then under",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "continue", f"expected normal completion; got {turn.outcome!r}"
    assert turn.output == "done"


# ---------------------------------------------------------------------------
# Regression (d): an earned hard-stop must survive a deferred-tool exit.
#
# Three consecutive over-cap requests reach the streak threshold inside the
# initial run; the next request stays within cap but defers on an approval-gated
# call, then a within-cap resume run follows. The within-cap requests reset the
# streak counter, so the hard-stop must be latched at the moment it was earned,
# not re-derived from the (now-zero) counter after the resume.
# ---------------------------------------------------------------------------


def _make_streak_then_deferred_agent() -> Agent:
    """Agent: 3 over-cap requests, then a within-cap request that defers, then resume.

    Protocol:
      model calls 1-3 — each streams MAX_TOOL_CALLS_PER_MODEL_REQUEST+1 noop calls
                        (3 consecutive violations → streak hits the hard-stop threshold)
      model call 4    — 1 noop + 1 approval-gated call → DeferredToolRequests (within cap)
      model call 5    — 1 noop (resume run, under cap)
      model call 6    — text "done"
    """
    call_count = {"n": 0}
    _OVER = MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        if 1 <= n <= 3:
            for i in range(_OVER):
                yield {
                    i: DeltaToolCall(
                        name="noop", json_args=f'{{"x":{i}}}', tool_call_id=f"c{n}x{i}"
                    )
                }
        elif n == 4:
            yield {0: DeltaToolCall(name="noop", json_args='{"x":0}', tool_call_id="c4x0")}
            yield {1: DeltaToolCall(name="needs_approval", json_args="{}", tool_call_id="c4ap")}
        elif n == 5:
            yield {0: DeltaToolCall(name="noop", json_args='{"x":0}', tool_call_id="c5x0")}
        else:
            yield "done"

    toolset: FunctionToolset = FunctionToolset()

    async def needs_approval(ctx: RunContext[CoDeps]) -> str:
        return "approved"

    async def noop(ctx: RunContext[CoDeps], x: int) -> str:
        return f"noop {x}"

    toolset.add_function(needs_approval, requires_approval=True)
    toolset.add_function(noop, requires_approval=False)

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_CallSeamToolset(toolset)],
    )


@pytest.mark.asyncio
async def test_hard_stop_survives_deferred_exit() -> None:
    """A streak that reaches the threshold must hard-stop even when a deferred exit
    and a within-cap resume reset the violation counter in between.

    The latched hard-stop still ends the turn, surfacing the model's final answer."""
    deps = _make_capped_deps(max_model_requests=90)
    agent = _make_streak_then_deferred_agent()
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="streak then defer",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "continue", f"expected surfaced answer; got {turn.outcome!r}"
    assert turn.output == "done"
    assert any("Tool-call cap exceeded" in s for s in frontend.statuses), (
        f"status must mention 'Tool-call cap exceeded'; got statuses: {frontend.statuses}"
    )


# ---------------------------------------------------------------------------
# Regression (e): a runaway that reaches the threshold inside a single run
# (no approval-gated tool, run completes normally) must still hard-stop. The
# hard-stop is not gated behind entering the approval loop.
# ---------------------------------------------------------------------------


def _make_single_run_runaway_agent() -> Agent:
    """Agent: 3 over-cap requests in one run, no approval tool, then text.

    Protocol:
      model calls 1-3 — each streams MAX_TOOL_CALLS_PER_MODEL_REQUEST+1 noop calls
      model call 4    — text "done"
    """
    call_count = {"n": 0}
    _OVER = MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        if 1 <= n <= 3:
            for i in range(_OVER):
                yield {
                    i: DeltaToolCall(
                        name="noop", json_args=f'{{"x":{i}}}', tool_call_id=f"c{n}x{i}"
                    )
                }
        else:
            yield "done"

    toolset: FunctionToolset = FunctionToolset()

    async def noop(ctx: RunContext[CoDeps], x: int) -> str:
        return f"noop {x}"

    toolset.add_function(noop, requires_approval=False)

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_CallSeamToolset(toolset)],
    )


@pytest.mark.asyncio
async def test_hard_stop_fires_in_single_run_without_approval() -> None:
    """A streak reaching the threshold within one run (no deferred tool) must hard-stop,
    surfacing the model's final answer once the cap latches."""
    deps = _make_capped_deps(max_model_requests=90)
    agent = _make_single_run_runaway_agent()
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="single run runaway",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "continue", f"expected surfaced answer; got {turn.outcome!r}"
    assert turn.output == "done"
    assert any("Tool-call cap exceeded" in s for s in frontend.statuses), (
        f"status must mention 'Tool-call cap exceeded'; got statuses: {frontend.statuses}"
    )


def _make_hard_stop_no_answer_agent() -> Agent:
    """Agent that hard-stops with no final text answer to surface.

    Protocol:
      model calls 1-3 — each streams MAX_TOOL_CALLS_PER_MODEL_REQUEST+1 noop calls
                        (3 consecutive violations → hard-stop latches)
      model call 4    — approval-gated call → DeferredToolRequests (initial run ends)
      model call 5    — approval-gated call again → DeferredToolRequests (resume run)
    The hard-stop break exits the approval loop with output still a
    DeferredToolRequests, so there is no usable string answer to surface.
    """
    call_count = {"n": 0}
    _OVER = MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        if 1 <= n <= 3:
            for i in range(_OVER):
                yield {
                    i: DeltaToolCall(
                        name="noop", json_args=f'{{"x":{i}}}', tool_call_id=f"c{n}x{i}"
                    )
                }
        else:
            yield {0: DeltaToolCall(name="needs_approval", json_args="{}", tool_call_id=f"ap{n}")}

    toolset: FunctionToolset = FunctionToolset()

    async def needs_approval(ctx: RunContext[CoDeps]) -> str:
        return "approved"

    async def noop(ctx: RunContext[CoDeps], x: int) -> str:
        return f"noop {x}"

    toolset.add_function(needs_approval, requires_approval=True)
    toolset.add_function(noop, requires_approval=False)

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_CallSeamToolset(toolset)],
    )


@pytest.mark.asyncio
async def test_hard_stop_without_answer_errors() -> None:
    """A hard-stop whose run produced no final text answer must still return error."""
    deps = _make_capped_deps(max_model_requests=90)
    agent = _make_hard_stop_no_answer_agent()
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="hard stop no answer",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "error", f"expected error outcome; got {turn.outcome!r}"
    assert turn.output is None
    assert any("Tool-call cap exceeded" in s for s in frontend.statuses), (
        f"status must mention 'Tool-call cap exceeded'; got statuses: {frontend.statuses}"
    )


# ---------------------------------------------------------------------------
# Regression (f): a within-cap autonomous loop (1 tool call per request, no
# approval gate, never terminates) stays inside a single run_stream_events call.
# It never trips the consecutive over-cap hard-stop, so only the SDK request
# cap (request_limit, checked before every request) can interrupt it mid-stream.
# ---------------------------------------------------------------------------


def _make_within_cap_runaway_agent() -> Agent:
    """Agent: one noop call on every model request, no approval tool, never stops.

    Each request stays within MAX_TOOL_CALLS_PER_MODEL_REQUEST, so the hard-stop
    never fires. A high sentinel bails to text only if the request cap failed to
    fire — so a broken cap fails the assertion instead of looping forever.
    """
    call_count = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        if n > 50:
            yield "done"
        else:
            yield {0: DeltaToolCall(name="noop", json_args='{"x":0}', tool_call_id=f"c{n}")}

    toolset: FunctionToolset = FunctionToolset()

    async def noop(ctx: RunContext[CoDeps], x: int) -> str:
        return f"noop {x}"

    toolset.add_function(noop, requires_approval=False)

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_CallSeamToolset(toolset)],
    )


@pytest.mark.asyncio
async def test_request_cap_interrupts_within_cap_single_run_loop() -> None:
    """A non-terminating ≤cap-per-request loop in one run must be interrupted mid-stream."""
    deps = _make_capped_deps(max_model_requests=3)
    agent = _make_within_cap_runaway_agent()
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="within-cap runaway",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "error", f"expected error outcome; got {turn.outcome!r}"
    assert any("Model-request cap" in s for s in frontend.statuses), (
        f"status must mention 'Model-request cap'; got statuses: {frontend.statuses}"
    )
    assert turn.model_requests == 3, (
        f"reported requests must equal the cap; got {turn.model_requests}"
    )


# ---------------------------------------------------------------------------
# Integration (g): graceful wrap-up nudge on the final allowed request before
# the cumulative model-request cap (history_processors.wrap_up_on_final_request).
#
# The nudge must (1) reach the model on the request issued when
# usage.requests == limit-1, (2) be suppressed when the cap is disabled, and
# (3) never persist into the returned turn history (stripped on both the
# answer-on-time success path and the ignored-nudge UsageLimitExceeded path).
# ---------------------------------------------------------------------------


def _messages_carry_wrap_up(messages: list[ModelMessage]) -> bool:
    """True if any ModelRequest in *messages* carries the wrap-up UserPromptPart."""
    return any(
        isinstance(part, UserPromptPart) and part.content == WRAP_UP_TEXT
        for msg in messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
    )


def _make_wrap_up_agent(
    *, honor_nudge: bool, done_at_call: int | None, saw_nudge: list[bool]
) -> Agent:
    """Agent with wrap_up_on_final_request registered, recording per-request nudge sightings.

    Each model call records whether the request it received carried the wrap-up text.
    When ``honor_nudge`` and the nudge is seen, it answers "done"; when ``done_at_call``
    is set it answers "done" on that call unconditionally; otherwise it emits one
    within-cap noop tool call (so the loop stays in a single run_stream_events call).
    """
    call_count = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        seen = _messages_carry_wrap_up(messages)
        saw_nudge.append(seen)
        if (done_at_call is not None and n >= done_at_call) or (honor_nudge and seen):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="noop", json_args='{"x":0}', tool_call_id=f"c{n}")}

    toolset: FunctionToolset = FunctionToolset()

    async def noop(ctx: RunContext[CoDeps], x: int) -> str:
        return f"noop {x}"

    toolset.add_function(noop, requires_approval=False)

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_CallSeamToolset(toolset)],
        history_processors=[wrap_up_on_final_request],
    )


@pytest.mark.asyncio
async def test_wrap_up_nudge_reaches_model_and_yields_answer() -> None:
    """The final allowed request carries the wrap-up nudge; honoring it returns an answer."""
    saw_nudge: list[bool] = []
    deps = _make_capped_deps(max_model_requests=3)
    agent = _make_wrap_up_agent(honor_nudge=True, done_at_call=None, saw_nudge=saw_nudge)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="wrap up please",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert any(saw_nudge), f"wrap-up nudge never reached the model; saw_nudge={saw_nudge}"
    assert turn.outcome == "continue", f"expected surfaced answer; got {turn.outcome!r}"
    assert turn.output == "done"
    assert not _messages_carry_wrap_up(turn.messages), (
        "wrap-up nudge must be stripped from persisted turn history"
    )


@pytest.mark.asyncio
async def test_wrap_up_nudge_suppressed_when_cap_disabled() -> None:
    """max_model_requests_per_turn=0 disables the cap, so no wrap-up nudge is injected."""
    saw_nudge: list[bool] = []
    deps = _make_capped_deps(max_model_requests=0)
    agent = _make_wrap_up_agent(honor_nudge=False, done_at_call=2, saw_nudge=saw_nudge)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="no cap",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert not any(saw_nudge), f"no nudge expected with cap disabled; saw_nudge={saw_nudge}"
    assert turn.outcome == "continue"
    assert turn.output == "done"


@pytest.mark.asyncio
async def test_wrap_up_nudge_ignored_still_strips_from_history() -> None:
    """If the model ignores the nudge and hits the cap, the nudge is still stripped."""
    saw_nudge: list[bool] = []
    deps = _make_capped_deps(max_model_requests=3)
    agent = _make_wrap_up_agent(honor_nudge=False, done_at_call=None, saw_nudge=saw_nudge)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await run_turn(
        agent=agent,
        user_input="ignore the nudge",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert any(saw_nudge), f"wrap-up nudge never reached the model; saw_nudge={saw_nudge}"
    assert turn.outcome == "error", f"expected cap error; got {turn.outcome!r}"
    assert not _messages_carry_wrap_up(turn.messages), (
        "wrap-up nudge must be stripped from persisted turn history on the error path"
    )
