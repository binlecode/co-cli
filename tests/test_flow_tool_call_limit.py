"""Tests for the per-model-request tool-call cap at the routing wrapper.

The cap's counting lives in _CallSeamToolset.call_tool: calls within one model
request past the cap get the rejection payload (the tool never executes). The
per-request counter RESET and the consecutive-violation streak/hard-stop are owned
by the orchestrator at the model-request node boundary and verified behaviorally
end-to-end in test_flow_model_request_cap (a turn that does / does not hard-stop,
and the fresh-budget reset between model requests).
"""

import json

import pytest
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.toolset import _CallSeamToolset
from co_cli.config.tuning import MAX_TOOL_CALLS_PER_MODEL_REQUEST
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.shell_backend import ShellBackend

CAP = MAX_TOOL_CALLS_PER_MODEL_REQUEST


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )


async def _build_routing_toolset(deps: CoDeps):
    """Return (_CallSeamToolset, tool) over a real one-tool FunctionToolset."""
    inner: FunctionToolset = FunctionToolset()

    async def echo(x: int) -> str:
        return f"ok{x}"

    inner.add_function(echo, requires_approval=False)
    routing = _CallSeamToolset(inner)
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
