"""OTEL enforce_tool_call_limit span coverage for the L0 brake (after_node_run hook)."""

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import CallToolsNode, RunContext, UserPromptNode
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.lifecycle import CoToolLifecycle
from co_cli.tools.shell_backend import ShellBackend


@pytest.fixture
def otel_lifecycle() -> tuple[CoToolLifecycle, InMemorySpanExporter]:
    """Build a CoToolLifecycle wired to an in-memory OTEL exporter via constructor injection."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("co-cli.tool_budget")
    return CoToolLifecycle(_tracer=tracer), exporter


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )


def _ctx(deps: CoDeps, run_step: int = 1) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage(), run_step=run_step)


def _call_tools_node() -> CallToolsNode:
    return CallToolsNode(model_response=ModelResponse(parts=[TextPart(content="")]))


async def _ok_handler(args) -> str:
    return "ok"


@pytest.mark.asyncio
async def test_enforce_tool_call_limit_span_on_saturation(otel_lifecycle):
    """8 calls in one turn: span fires with issued=8, allowed=6, rejected=2, limit_exceeded=True."""
    lifecycle, exporter = otel_lifecycle
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    for _ in range(8):
        await lifecycle.wrap_tool_execute(
            ctx, call=None, tool_def=None, args=None, handler=_ok_handler
        )

    await lifecycle.after_node_run(ctx, node=_call_tools_node(), result=None)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "tool_budget.enforce_tool_call_limit" in spans, (
        f"Expected enforce_tool_call_limit span; got: {list(spans)}"
    )
    attrs = dict(spans["tool_budget.enforce_tool_call_limit"].attributes)
    assert attrs["tool_calls.issued"] == 8
    assert attrs["tool_calls.allowed"] == MAX_TOOL_CALLS_PER_MODEL_TURN
    assert attrs["tool_calls.rejected"] == 8 - MAX_TOOL_CALLS_PER_MODEL_TURN
    assert attrs["tool_calls.limit_exceeded"] is True
    assert attrs["tool_calls.limit"] == MAX_TOOL_CALLS_PER_MODEL_TURN


@pytest.mark.asyncio
async def test_enforce_tool_call_limit_span_within_cap(otel_lifecycle):
    """3 calls in one turn: span fires with limit_exceeded=False."""
    lifecycle, exporter = otel_lifecycle
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    for _ in range(3):
        await lifecycle.wrap_tool_execute(
            ctx, call=None, tool_def=None, args=None, handler=_ok_handler
        )

    await lifecycle.after_node_run(ctx, node=_call_tools_node(), result=None)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "tool_budget.enforce_tool_call_limit" in spans, (
        "enforce_tool_call_limit span must fire even for under-cap turns"
    )
    attrs = dict(spans["tool_budget.enforce_tool_call_limit"].attributes)
    assert attrs["tool_calls.issued"] == 3
    assert attrs["tool_calls.limit_exceeded"] is False


@pytest.mark.asyncio
async def test_enforce_tool_call_limit_span_skipped_for_non_call_tools_node(otel_lifecycle):
    """after_node_run must not emit the span when node is not a CallToolsNode."""
    lifecycle, exporter = otel_lifecycle
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    for _ in range(8):
        await lifecycle.wrap_tool_execute(
            ctx, call=None, tool_def=None, args=None, handler=_ok_handler
        )

    node = UserPromptNode(user_prompt="hi")
    await lifecycle.after_node_run(ctx, node=node, result=None)

    spans = [s.name for s in exporter.get_finished_spans()]
    assert "tool_budget.enforce_tool_call_limit" not in spans, (
        f"Span must not fire for non-CallToolsNode; got: {spans}"
    )
