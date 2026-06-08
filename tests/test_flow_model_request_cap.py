"""Tests for model-request cap and tool-call-cap hard-stop in run_turn().

Production paths:
  co_cli/config/llm.py            — LlmSettings.max_model_requests_per_turn
  co_cli/context/orchestrate.py   — _check_turn_caps, run_turn
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.toolset import _CallSeamToolset
from co_cli.config.core import load_config
from co_cli.config.llm import DEFAULT_MAX_MODEL_REQUESTS_PER_TURN, LlmSettings
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_REQUEST

# ---------------------------------------------------------------------------
# Unit: LlmSettings defaults and env var override
# ---------------------------------------------------------------------------


def test_max_model_requests_default_is_90() -> None:
    """LlmSettings() with no overrides yields max_model_requests_per_turn == 90."""
    s = LlmSettings(provider="ollama")
    assert s.max_model_requests_per_turn == DEFAULT_MAX_MODEL_REQUESTS_PER_TURN
    assert s.max_model_requests_per_turn == 90


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
        model_max_ctx=config.llm.max_ctx,
    )


# ---------------------------------------------------------------------------
# Integration (b): model-request cap via run_turn
#
# Strategy: build a stub agent with one approval-required tool. The model
# returns that tool call twice (model requests 1 and 2), triggering the approval
# loop to run two resume segments. On the third model call, it returns text.
# With max_model_requests_per_turn=3, the cap fires after the approval loop.
# ---------------------------------------------------------------------------


def _make_model_request_cap_agent() -> Agent:
    """Agent that accumulates 3 model requests via approval-loop resume segments.

    Protocol:
      model call 1 — yield approval-required tool call (initial segment)
      model call 2 — yield approval-required tool call (resume segment 1)
      model call 3 — yield text "done" (resume segment 2)

    Total = 3 ModelResponse messages.  With max_model_requests_per_turn=3, the cap
    fires after _run_approval_loop returns without the hard-stop being set.
    """
    call_count = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        n = call_count["n"]
        if n <= 2:
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
    """run_turn with max_model_requests_per_turn=3 must stop after 3 model requests.

    The stub model returns approval-required tool calls twice, driving two
    approval-resume segments. After model request 3 (resume segment 2's final
    model call), the cap fires before the turn can return successfully.
    """
    deps = _make_capped_deps(max_model_requests=3)
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
    assert turn.model_requests >= 3, (
        f"must have run at least 3 model requests; got {turn.model_requests}"
    )


# ---------------------------------------------------------------------------
# Integration (c): tool-call-cap hard-stop via run_turn
#
# Strategy: one approval-required tool (initial segment), then 3 rounds of
# MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1 noop calls in the resume segment.
# Each over-cap round increments the streak in the routing wrapper.  After 3
# consecutive violations, _run_approval_loop sets tool_cap_hard_stop and breaks.
# ---------------------------------------------------------------------------


def _make_hard_stop_agent() -> Agent:
    """Agent that causes 3 consecutive tool-cap violations inside the approval loop.

    Protocol:
      model call 1 — approval-required call (initial segment → DeferredToolRequests)
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
async def test_hard_stop_fires_after_consecutive_violations() -> None:
    """run_turn must return outcome='error' with hard-stop status after 3 consecutive violations.

    Flow:
      initial segment — model streams approval-required tool → DeferredToolRequests
      resume segment  — 3 rounds of MAX_TOOL_CALLS_PER_MODEL_REQUEST+1 noop calls trigger
                        3 consecutive tool-cap violations → _run_approval_loop hard-stops
      _check_turn_caps → outcome='error', status 'Tool-call cap exceeded'
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

    assert turn.outcome == "error", f"expected error outcome; got {turn.outcome!r}"
    assert any("Tool-call cap exceeded" in s for s in frontend.statuses), (
        f"status must mention 'Tool-call cap exceeded'; got statuses: {frontend.statuses}"
    )


def _make_over_then_under_cap_agent() -> Agent:
    """Agent: one over-cap request followed by an under-cap request, then text.

    Protocol:
      model call 1 — approval-required call (initial segment → DeferredToolRequests)
      model call 2 — MAX_TOOL_CALLS_PER_MODEL_REQUEST+1 noop calls (one over cap)
      model call 3 — exactly 1 noop call (under cap → request behaves)
      model call 4 — text "done"

    The under-cap final tool request must reset the streak at the segment boundary,
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

    Proves per-request granularity: the streak is finalized to 0 at the segment
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
