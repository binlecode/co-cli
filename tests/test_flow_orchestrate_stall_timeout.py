"""Tests for the model-generation STALL timeout in _execute_run().

Production path: co_cli/agent/orchestrate.py — the `asyncio.timeout(stall_window)`
guard around the agent.iter() per-node loop, where stall_window =
llm.run_stall_timeout_secs. The guard is a stall detector, NOT an absolute run
deadline: _StallTimer is armed on entering a model-request node and re-armed on every
event streamed from it, and DISARMED on entering a call-tools node. So:

- a model that goes silent (no progress) is killed → TurnResult(outcome="error");
- a model that keeps streaming, however long, survives;
- a long-running tool is bounded by its own timeout, not by this loop.

Driven with a fake model (FunctionModel) and a fake sleeping tool so the timing is
deterministic; the real run_turn / SessionAgent / _execute_run code runs unchanged.
The stall window is set small via config (llm.run_stall_timeout_secs) so the tests run
in well under a second of wall-time each while preserving a 2x margin against jitter.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.toolsets.function import FunctionToolset
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.build import build_orchestrator
from co_cli.agent.core import build_native_toolset
from co_cli.agent.orchestrate import run_turn
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import LlmModel
from co_cli.tools.shell_backend import ShellBackend

_TOOLSET, _TOOL_INDEX = build_native_toolset()

_STALL_WINDOW = 0.5

# Production config with the stall window shrunk to _STALL_WINDOW — exercises the real
# llm.run_stall_timeout_secs override path instead of monkeypatching a module constant.
_STALL_SETTINGS = SETTINGS_NO_MCP.model_copy(
    update={
        "llm": SETTINGS_NO_MCP.llm.model_copy(update={"run_stall_timeout_secs": _STALL_WINDOW})
    }
)


def _make_deps(model: FunctionModel, toolset=None, tool_catalog=None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=LlmModel(
            model=model,
            settings=_STALL_SETTINGS.llm.noreason_model_settings(),
            settings_noreason=_STALL_SETTINGS.llm.noreason_model_settings(),
        ),
        toolset=toolset if toolset is not None else _TOOLSET,
        tool_catalog=tool_catalog if tool_catalog is not None else _TOOL_INDEX,
        config=_STALL_SETTINGS,
        session=CoSessionState(),
        model_max_context_tokens=_STALL_SETTINGS.llm.max_context_tokens,
    )


async def _run(deps: CoDeps):
    agent = build_orchestrator(ORCHESTRATOR_SPEC, deps)
    return await run_turn(
        agent=agent,
        user_input="go",
        deps=deps,
        message_history=[],
        model_settings=None,
        frontend=SilentFrontend(),
    )


@pytest.mark.asyncio
async def test_silent_model_trips_stall_timeout() -> None:
    """A model that produces no progress within the window → outcome='error'."""

    async def stall(messages: list, info: AgentInfo) -> AsyncIterator[str]:
        await asyncio.sleep(_STALL_WINDOW * 2)
        yield "too late"

    turn = await _run(_make_deps(FunctionModel(stream_function=stall)))

    assert turn.outcome == "error"


@pytest.mark.asyncio
async def test_steady_progress_outlasts_window() -> None:
    """A model streaming steadily for longer than one window survives.

    Total stream time (4 x 0.2s = 0.8s) exceeds the 0.5s window, but no single
    inter-event gap does — so an idle timer never fires while an absolute deadline
    would have killed it. Confirms the guard is idle-based, not a run-total cap.
    """

    async def drip(messages: list, info: AgentInfo) -> AsyncIterator[str]:
        for _ in range(4):
            await asyncio.sleep(_STALL_WINDOW * 0.4)
            yield "tick "

    turn = await _run(_make_deps(FunctionModel(stream_function=drip)))

    assert turn.outcome == "continue"


@pytest.mark.asyncio
async def test_long_tool_does_not_trip_stall_timeout() -> None:
    """A tool that runs longer than the window does not trip the stall timer.

    The timer is disarmed on the call-tools node, so tool execution time is bounded by
    the tool's own budget, not this loop. This is the regression guard for the
    run-ceiling-below-tool-budget contradiction.
    """

    async def slow_tool() -> str:
        await asyncio.sleep(_STALL_WINDOW * 2)
        return "tool done"

    toolset: FunctionToolset = FunctionToolset()
    toolset.add_function(slow_tool, takes_ctx=False)

    state = {"calls": 0}

    async def call_then_answer(messages: list, info: AgentInfo) -> AsyncIterator:
        state["calls"] += 1
        if state["calls"] == 1:
            yield {0: DeltaToolCall(name="slow_tool", json_args="{}", tool_call_id="t1")}
        else:
            yield "all done"

    deps = _make_deps(
        FunctionModel(stream_function=call_then_answer),
        toolset=toolset,
        tool_catalog={},
    )

    turn = await _run(deps)

    assert turn.outcome == "continue"
