"""Tests for the per-model-request tool-call cap at the routing wrapper.

The cap lives in _RoutingToolset.call_tool: calls within a run_step past the cap
get the rejection payload (the tool never executes), and a run_step transition
resets the per-request counter so each model request gets a fresh budget. The
consecutive-violation streak and its hard-stop are verified behaviorally end-to-end
in test_flow_model_request_cap (a turn that does / does not hard-stop).
"""

import json

import pytest
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.toolset import _RoutingToolset
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_REQUEST

CAP = MAX_TOOL_CALLS_PER_MODEL_REQUEST


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )


async def _build_routing_toolset(deps: CoDeps):
    """Return (_RoutingToolset, tool) over a real one-tool FunctionToolset."""
    inner: FunctionToolset = FunctionToolset()

    async def echo(x: int) -> str:
        return f"ok{x}"

    inner.add_function(echo, requires_approval=False)
    routing = _RoutingToolset(inner)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), run_step=1)
    tools = await routing.get_tools(ctx)
    return routing, tools["echo"]


def _ctx(deps: CoDeps, run_step: int) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage(), run_step=run_step)


@pytest.mark.asyncio
async def test_calls_up_to_cap_execute_then_excess_rejected():
    deps = _make_deps()
    routing, tool = await _build_routing_toolset(deps)
    ctx = _ctx(deps, run_step=1)

    results = [await routing.call_tool("echo", {"x": i}, ctx, tool) for i in range(CAP + 2)]

    assert results[:CAP] == [f"ok{i}" for i in range(CAP)]
    for rejected in results[CAP:]:
        payload = json.loads(rejected)
        assert payload["error"] == "max_tool_calls_per_model_request_exceeded"
        assert str(CAP) in payload["guidance"]


@pytest.mark.asyncio
async def test_run_step_transition_resets_per_request_counter():
    deps = _make_deps()
    routing, tool = await _build_routing_toolset(deps)

    for step in (1, 2):
        ctx = _ctx(deps, run_step=step)
        results = [await routing.call_tool("echo", {"x": i}, ctx, tool) for i in range(CAP)]
        assert results == [f"ok{i}" for i in range(CAP)], (
            f"all {CAP} calls must execute at run_step={step} after the counter resets"
        )
