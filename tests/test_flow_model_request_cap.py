"""Tests for the model-request cap, tool-call-cap hard-stop, and wrap-up nudge in the owned loop.

Production paths:
  co_cli/config/llm.py        — LlmSettings.max_model_requests_per_turn, resolve_request_limit
  co_cli/agent/loop.py        — _orchestrator_step_loop (request cap + tool-cap hard stop),
                                _last_assistant_text, TOOL_CAP_NO_ANSWER_TEXT
  co_cli/agent/_instructions.py — wrap_up_prompt (final-request wrap-up nudge)

The cap behaviors are driven end-to-end through ``run_turn_owned`` with pydantic-ai's
``FunctionModel`` (the SDK's deterministic agent driver) — no real LLM call. The
ToolCapState shed/latch arithmetic itself is pinned in test_flow_owned_tool_cap_state.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.core import assemble_routing_toolset
from co_cli.agent.loop import TOOL_CAP_NO_ANSWER_TEXT, run_turn_owned
from co_cli.config.core import load_config
from co_cli.config.llm import MAX_MODEL_REQUESTS_PER_TURN, LlmSettings
from co_cli.config.tuning import MAX_TOOL_CALLS_PER_MODEL_REQUEST
from co_cli.deps import (
    CoDeps,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import LlmModel
from co_cli.tools.shell_backend import ShellBackend

_OVER = MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1


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
# Shared harness: owned-loop deps wired with a noop native toolset + FunctionModel.
# ---------------------------------------------------------------------------


def _info(name: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description="test",
        is_approval_required=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
        is_concurrent_safe=True,
    )


def _make_deps(model: FunctionModel, *, max_model_requests: int) -> CoDeps:
    config = SETTINGS_NO_MCP.model_copy(
        update={
            "llm": SETTINGS_NO_MCP.llm.model_copy(
                update={"max_model_requests_per_turn": max_model_requests}
            )
        }
    )
    inner: FunctionToolset = FunctionToolset()

    async def noop(ctx: RunContext[CoDeps], x: int = 0) -> str:
        return f"noop {x}"

    inner.add_function(noop, requires_approval=False)
    deps = CoDeps(
        shell=ShellBackend(),
        model=LlmModel(
            model=model,
            settings=config.llm.noreason_model_settings(),
            settings_noreason=config.llm.noreason_model_settings(),
        ),
        config=config,
        session=CoSessionState(),
        toolset=assemble_routing_toolset(inner, []),
        tool_catalog={"noop": _info("noop")},
        model_max_context_tokens=config.llm.max_context_tokens,
    )
    return deps


async def _run(deps: CoDeps, frontend: HeadlessFrontend, user_input: str = "go"):
    return await run_turn_owned(
        user_input=user_input,
        deps=deps,
        message_history=[],
        frontend=frontend,
    )


# ---------------------------------------------------------------------------
# Model-request cap: a non-terminating one-call-per-step loop is cut off.
# ---------------------------------------------------------------------------


def _within_cap_runaway_model() -> FunctionModel:
    """Issues one noop call on every step, never stops on its own."""

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        yield {0: DeltaToolCall(name="noop", json_args='{"x":0}', tool_call_id="c")}

    return FunctionModel(stream_function=stream_fn)


@pytest.mark.asyncio
async def test_model_request_cap_stops_runaway_loop() -> None:
    """A ≤cap-per-step loop that never terminates is stopped by the model-request cap.

    With max_model_requests_per_turn=3, the loop completes 3 requests and the 4th is
    blocked before it is sent (the turn errors, reporting exactly the cap count).
    """
    deps = _make_deps(_within_cap_runaway_model(), max_model_requests=3)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await _run(deps, frontend)

    assert turn.outcome == "error", f"expected cap error; got {turn.outcome!r}"
    assert any("Model-request cap" in s for s in frontend.statuses), frontend.statuses
    assert turn.model_requests == 3, turn.model_requests


# ---------------------------------------------------------------------------
# Tool-call-cap hard-stop: consecutive over-cap steps trip the circuit breaker.
# ---------------------------------------------------------------------------


def _hard_stop_then_text_model() -> FunctionModel:
    """Streams over-cap noop bursts until the hard stop latches, with text on step 1.

    Step 1 emits visible text then an over-cap burst; steps 2+ emit over-cap bursts.
    After TOOL_CAP_HARD_STOP_CONSECUTIVE consecutive over-cap steps the owned loop
    latches the hard stop and surfaces the salvaged text.
    """
    state = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        state["n"] += 1
        if state["n"] == 1:
            yield "partial progress"
        for i in range(_OVER):
            yield {
                i: DeltaToolCall(
                    name="noop", json_args=f'{{"x":{i}}}', tool_call_id=f"s{state['n']}x{i}"
                )
            }

    return FunctionModel(stream_function=stream_fn)


@pytest.mark.asyncio
async def test_tool_cap_hard_stop_surfaces_salvaged_text() -> None:
    """Consecutive over-cap steps trip the hard stop; the salvaged assistant text surfaces."""
    deps = _make_deps(_hard_stop_then_text_model(), max_model_requests=90)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await _run(deps, frontend)

    assert turn.outcome == "continue", f"expected graceful close; got {turn.outcome!r}"
    assert turn.output == "partial progress"
    assert any("Tool-call cap exceeded" in s for s in frontend.statuses), frontend.statuses


def _hard_stop_no_text_model() -> FunctionModel:
    """Streams over-cap noop bursts on every step with no visible text anywhere."""

    state = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        state["n"] += 1
        for i in range(_OVER):
            yield {
                i: DeltaToolCall(
                    name="noop", json_args=f'{{"x":{i}}}', tool_call_id=f"s{state['n']}x{i}"
                )
            }

    return FunctionModel(stream_function=stream_fn)


@pytest.mark.asyncio
async def test_tool_cap_hard_stop_without_text_returns_canned_message() -> None:
    """A hard stop with no visible text returns the canned message — never error/None."""
    deps = _make_deps(_hard_stop_no_text_model(), max_model_requests=90)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await _run(deps, frontend)

    assert turn.outcome == "continue", f"expected graceful close; got {turn.outcome!r}"
    assert turn.output == TOOL_CAP_NO_ANSWER_TEXT
    assert any("Tool-call cap exceeded" in s for s in frontend.statuses), frontend.statuses


# ---------------------------------------------------------------------------
# Wrap-up nudge: the final allowed step carries the nudge; honoring it answers.
# ---------------------------------------------------------------------------


def _wrap_up_model(*, honor_nudge: bool, saw_nudge: list[bool]) -> FunctionModel:
    """Records whether each step's instructions carry the wrap-up text; optionally honors it.

    The owned loop assembles wrap_up_prompt into the request instructions, which reach the
    FunctionModel via ``info.instructions``. When ``honor_nudge`` and the nudge is present
    the model answers "done"; otherwise it emits a within-cap noop call (loop continues).
    """
    from co_cli.agent._instructions import WRAP_UP_TEXT

    state = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        state["n"] += 1
        seen = info.instructions is not None and WRAP_UP_TEXT in info.instructions
        saw_nudge.append(seen)
        if honor_nudge and seen:
            yield "done"
        else:
            yield {
                0: DeltaToolCall(name="noop", json_args='{"x":0}', tool_call_id=f"c{state['n']}")
            }

    return FunctionModel(stream_function=stream_fn)


def _request_instructions_carry_wrap_up(messages: list[ModelMessage]) -> bool:
    from co_cli.agent._instructions import WRAP_UP_TEXT

    return any(
        isinstance(msg, ModelRequest)
        and msg.instructions is not None
        and WRAP_UP_TEXT in msg.instructions
        for msg in messages
    )


def _messages_carry_wrap_up_part(messages: list[ModelMessage]) -> bool:
    from co_cli.agent._instructions import WRAP_UP_TEXT

    return any(
        isinstance(part, UserPromptPart) and part.content == WRAP_UP_TEXT
        for msg in messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
    )


@pytest.mark.asyncio
async def test_wrap_up_nudge_reaches_model_and_yields_answer() -> None:
    """The final allowed step carries the wrap-up nudge; honoring it returns an answer."""
    saw_nudge: list[bool] = []
    deps = _make_deps(_wrap_up_model(honor_nudge=True, saw_nudge=saw_nudge), max_model_requests=3)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await _run(deps, frontend, user_input="wrap up please")

    assert any(saw_nudge), f"wrap-up nudge never reached the model; saw_nudge={saw_nudge}"
    assert turn.outcome == "continue", f"expected surfaced answer; got {turn.outcome!r}"
    assert turn.output == "done"
    assert not _messages_carry_wrap_up_part(turn.messages), (
        "wrap-up nudge must never appear as a UserPromptPart in persisted turn history"
    )
    assert not _request_instructions_carry_wrap_up(turn.messages), (
        "the dynamic nudge must not be replayed in persisted ModelRequest.instructions"
    )


@pytest.mark.asyncio
async def test_wrap_up_nudge_suppressed_when_cap_disabled() -> None:
    """max_model_requests_per_turn=0 disables the cap, so no wrap-up nudge is injected.

    The model answers on its second step regardless; with the cap disabled the nudge
    predicate (request_count == limit-1) can never fire.
    """
    saw_nudge: list[bool] = []

    state = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        from co_cli.agent._instructions import WRAP_UP_TEXT

        state["n"] += 1
        saw_nudge.append(info.instructions is not None and WRAP_UP_TEXT in info.instructions)
        if state["n"] >= 2:
            yield "done"
        else:
            yield {
                0: DeltaToolCall(name="noop", json_args='{"x":0}', tool_call_id=f"c{state['n']}")
            }

    deps = _make_deps(FunctionModel(stream_function=stream_fn), max_model_requests=0)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await _run(deps, frontend, user_input="no cap")

    assert not any(saw_nudge), f"no nudge expected with cap disabled; saw_nudge={saw_nudge}"
    assert turn.outcome == "continue"
    assert turn.output == "done"


@pytest.mark.asyncio
async def test_wrap_up_nudge_ignored_never_persists_as_part() -> None:
    """If the model ignores the nudge and hits the cap, the nudge never persists as a part."""
    saw_nudge: list[bool] = []
    deps = _make_deps(_wrap_up_model(honor_nudge=False, saw_nudge=saw_nudge), max_model_requests=3)
    frontend = HeadlessFrontend(approval_response="y")

    turn = await _run(deps, frontend, user_input="ignore the nudge")

    assert any(saw_nudge), f"wrap-up nudge never reached the model; saw_nudge={saw_nudge}"
    assert turn.outcome == "error", f"expected cap error; got {turn.outcome!r}"
    assert not _messages_carry_wrap_up_part(turn.messages), (
        "wrap-up nudge must never appear as a UserPromptPart, even on the error path"
    )
