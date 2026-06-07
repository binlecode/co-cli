"""Span-tree emission via a real run_turn, plus the routing-wrapper error path.

The agent/model/tool spans are emitted on the seams co owns after the capability
removal: the agent span at the run call site (_execute_stream_segment), the chat
span in SurrogateRecoveryModel, and the tool span in _RoutingToolset. This proves
the co tail / co trace tree is preserved at parity end-to-end.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.toolset import _RoutingToolset
from co_cli.context.orchestrate import run_turn
from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import LlmModel
from co_cli.llm.surrogate_recovery_model import SurrogateRecoveryModel
from co_cli.observability import tracing
from co_cli.tools.shell_backend import ShellBackend


@pytest.fixture(autouse=True)
def _reset_tracing() -> None:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    tracing._COMPILED_PATTERNS = []
    tracing._SESSION_ID.set(None)
    tracing._TRACE_ID.set(None)
    tracing._SPAN_STACK.set(())


def _read_records(log_path: Path) -> list[dict]:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in logger.handlers:
        handler.flush()
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _by_kind(records: list[dict], kind: str) -> list[dict]:
    return [r for r in records if r["kind"] == kind]


@pytest.mark.asyncio
async def test_turn_emits_agent_model_tool_span_tree(tmp_path: Path) -> None:
    """One streamed turn that calls a tool then answers emits the co.turn root, one
    agent span, two model spans (with non-zero streamed token counts), and one tool
    span — all correctly parented."""
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    call_count = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            yield {0: DeltaToolCall(name="echo", json_args='{"text":"hi"}', tool_call_id="c1")}
        else:
            yield "done"

    model = SurrogateRecoveryModel(FunctionModel(stream_function=stream_fn))

    inner: FunctionToolset = FunctionToolset()

    async def echo(text: str) -> str:
        return f"echoed: {text}"

    inner.add_function(echo, requires_approval=False)
    agent: Agent = Agent(
        model,
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
        toolsets=[_RoutingToolset(inner)],
    )
    echo_info = ToolInfo(
        name="echo",
        description="echo",
        approval=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        model=LlmModel(model=model, settings=None),
        tool_index={"echo": echo_info},
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )

    turn = await run_turn(
        agent=agent,
        user_input="hello",
        deps=deps,
        message_history=[],
        frontend=HeadlessFrontend(),
    )
    assert turn.output == "done"

    records = _read_records(log)
    turn_records = _by_kind(records, "co")
    agent_records = _by_kind(records, "agent")
    model_records = _by_kind(records, "model")
    tool_records = _by_kind(records, "tool")

    assert len(agent_records) == 1
    assert len(model_records) == 2
    assert len(tool_records) == 1

    turn_id = next(r["span_id"] for r in turn_records if r["name"] == "co.turn")
    agent_id = agent_records[0]["span_id"]
    assert agent_records[0]["parent_span_id"] == turn_id
    for r in model_records + tool_records:
        assert r["parent_span_id"] == agent_id

    # The streamed assembled response must carry non-zero token counts (the
    # streaming assembled-response risk), and the tool span its co.tool.* metadata.
    assert all(r["attributes"]["co.model.tokens.input"] > 0 for r in model_records)
    assert tool_records[0]["attributes"]["co.tool.name"] == "echo"
    assert tool_records[0]["attributes"]["co.tool.source"] == "native"


@pytest.mark.asyncio
async def test_tool_error_emits_error_span_and_clears_stack(tmp_path: Path) -> None:
    """A tool that raises produces a tool ERROR record and leaves the span stack empty."""
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    inner: FunctionToolset = FunctionToolset()

    async def boom() -> str:
        raise ValueError("tool exploded")

    inner.add_function(boom, requires_approval=False)
    routing = _RoutingToolset(inner)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), run_step=1)
    tool = (await routing.get_tools(ctx))["boom"]

    with pytest.raises(ValueError, match="tool exploded"):
        await routing.call_tool("boom", {}, ctx, tool)

    assert tracing._SPAN_STACK.get() == (), "span stack must be empty after the error"
    error_recs = [r for r in _by_kind(_read_records(log), "tool") if r["status"] == "ERROR"]
    assert len(error_recs) == 1
    assert "tool exploded" in error_recs[0]["status_msg"]
