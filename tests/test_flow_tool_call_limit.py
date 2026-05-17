"""Tests for the per-model-turn tool-call brake (L0 cap) — behavior + recorded events."""

import asyncio
import json

import pytest
from pydantic_ai import CallToolsNode, RunContext, UserPromptNode
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.observability import tracing
from co_cli.tools.lifecycle import CoToolLifecycle
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN, make_exceeded_payload


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


async def _ok_handler(args) -> str:
    return "ok"


def _call_tools_node() -> CallToolsNode:
    return CallToolsNode(model_response=ModelResponse(parts=[TextPart(content="")]))


@pytest.fixture
def lifecycle_with_span():
    """Open a parent span before each test so add_event() lands on a real span.

    Returns ``(CoToolLifecycle, span_dict)`` — read ``span_dict["events"]`` to
    inspect events emitted by the capability hooks. The fixture pops the span
    on teardown without emitting (raw inspection rather than log read)."""
    tracing._SPAN_STACK.set(())
    tracing._TRACE_ID.set(None)
    span = tracing.push_span("test_parent", kind="agent")
    try:
        yield CoToolLifecycle(), span
    finally:
        stack = tracing._SPAN_STACK.get()
        if stack and stack[-1] is span:
            tracing._SPAN_STACK.set(stack[:-1])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reset_for_turn_resets_all_per_turn_fields():
    """Every per-turn field in CoRuntimeState must be cleared by reset_for_turn().

    This test is the structural enforcement for the per-turn contract. Add new
    per-turn fields here when you add them to CoRuntimeState.reset_for_turn().
    """
    rt = CoRuntimeState()
    rt.turn_usage = RunUsage(requests=1, input_tokens=10, output_tokens=10)
    rt.tool_progress_callback = lambda msg: None
    rt.status_callback = lambda msg: None
    rt.resume_tool_names = frozenset(["some_tool"])
    rt.compaction_applied_this_turn = True
    rt.current_request_tokens_estimate = 42

    rt.reset_for_turn()

    assert rt.turn_usage is None
    assert rt.tool_progress_callback is None
    assert rt.status_callback is None
    assert rt.resume_tool_names is None
    assert rt.compaction_applied_this_turn is False
    assert rt.current_request_tokens_estimate is None


@pytest.mark.asyncio
async def test_brake_allows_up_to_cap():
    """All 6 calls within the cap must reach the handler and return 'ok'."""
    lifecycle = CoToolLifecycle()
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    results = []
    for _ in range(MAX_TOOL_CALLS_PER_MODEL_TURN):
        result = await lifecycle.wrap_tool_execute(
            ctx,
            call=None,
            tool_def=None,
            args=None,
            handler=_ok_handler,
        )
        results.append(result)

    assert all(r == "ok" for r in results), (
        f"All {MAX_TOOL_CALLS_PER_MODEL_TURN} calls must reach handler; got: {results}"
    )


@pytest.mark.asyncio
async def test_brake_rejects_above_cap():
    """Calls 7 and 8 must be rejected; their payloads must contain error and guidance."""
    lifecycle = CoToolLifecycle()
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    results = []
    for _ in range(8):
        result = await lifecycle.wrap_tool_execute(
            ctx,
            call=None,
            tool_def=None,
            args=None,
            handler=_ok_handler,
        )
        results.append(result)

    # First 6 allowed
    assert all(r == "ok" for r in results[:MAX_TOOL_CALLS_PER_MODEL_TURN])

    # Last 2 rejected
    for rejected in results[MAX_TOOL_CALLS_PER_MODEL_TURN:]:
        payload = json.loads(rejected)
        assert payload["error"] == "max_tool_calls_per_turn_exceeded"
        assert "guidance" in payload
        guidance = payload["guidance"]
        assert str(MAX_TOOL_CALLS_PER_MODEL_TURN) in guidance
        # issued count (7 or 8) must appear in guidance
        issued_in_guidance = any(
            str(i) in guidance for i in range(MAX_TOOL_CALLS_PER_MODEL_TURN + 1, 9)
        )
        assert issued_in_guidance, f"guidance must contain the issued count; got: {guidance!r}"


@pytest.mark.asyncio
async def test_run_step_transition_resets_counter():
    """6 calls at run_step=1 (all allowed), then 6 calls at run_step=2 (all allowed again)."""
    lifecycle = CoToolLifecycle()
    deps = _make_deps()

    # run_step=1: 6 calls, all should pass
    ctx1 = _ctx(deps, run_step=1)
    for _ in range(MAX_TOOL_CALLS_PER_MODEL_TURN):
        result = await lifecycle.wrap_tool_execute(
            ctx1,
            call=None,
            tool_def=None,
            args=None,
            handler=_ok_handler,
        )
        assert result == "ok", "All calls in run_step=1 must be allowed"

    # run_step=2: counter must reset — 6 calls should all pass again
    ctx2 = _ctx(deps, run_step=2)
    for _ in range(MAX_TOOL_CALLS_PER_MODEL_TURN):
        result = await lifecycle.wrap_tool_execute(
            ctx2,
            call=None,
            tool_def=None,
            args=None,
            handler=_ok_handler,
        )
        assert result == "ok", "All calls in run_step=2 must be allowed after counter reset"


@pytest.mark.asyncio
async def test_concurrency_exactly_cap_dispatched():
    """asyncio.gather of 8 concurrent calls at the same run_step must dispatch exactly 6."""
    lifecycle = CoToolLifecycle()
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    handler_call_count = 0

    async def counting_handler(args) -> str:
        nonlocal handler_call_count
        handler_call_count += 1
        return "ok"

    results = await asyncio.gather(
        *[
            lifecycle.wrap_tool_execute(
                ctx,
                call=None,
                tool_def=None,
                args=None,
                handler=counting_handler,
            )
            for _ in range(8)
        ]
    )

    assert handler_call_count == MAX_TOOL_CALLS_PER_MODEL_TURN, (
        f"Expected exactly {MAX_TOOL_CALLS_PER_MODEL_TURN} handler calls, got {handler_call_count}"
    )
    ok_count = sum(1 for r in results if r == "ok")
    assert ok_count == MAX_TOOL_CALLS_PER_MODEL_TURN


def test_guidance_contains_interpolated_values():
    """make_exceeded_payload guidance must contain the cap value and the issued count."""
    issued = 9
    payload = make_exceeded_payload(issued)
    guidance = payload["guidance"]
    assert str(MAX_TOOL_CALLS_PER_MODEL_TURN) in guidance, (
        f"guidance must contain cap={MAX_TOOL_CALLS_PER_MODEL_TURN}; got: {guidance!r}"
    )
    assert str(issued) in guidance, f"guidance must contain issued={issued}; got: {guidance!r}"


# ---------------------------------------------------------------------------
# tool_budget.enforce_tool_call_limit — event on active capability span
# ---------------------------------------------------------------------------


def _find_event(span: dict, name: str) -> dict | None:
    for event in span["events"]:
        if event["name"] == name:
            return event
    return None


@pytest.mark.asyncio
async def test_enforce_tool_call_limit_event_on_saturation(lifecycle_with_span):
    """8 calls in one turn: event fires with issued=8, allowed=6, rejected=2, limit_exceeded=True."""
    lifecycle, parent_span = lifecycle_with_span
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    for _ in range(8):
        await lifecycle.wrap_tool_execute(
            ctx, call=None, tool_def=None, args=None, handler=_ok_handler
        )

    await lifecycle.after_node_run(ctx, node=_call_tools_node(), result=None)

    event = _find_event(parent_span, "tool_budget.enforce_tool_call_limit")
    assert event is not None, (
        f"Expected enforce_tool_call_limit event; got events: {[e['name'] for e in parent_span['events']]}"
    )
    attrs = event["attributes"]
    assert attrs["tool_calls.issued"] == 8
    assert attrs["tool_calls.allowed"] == MAX_TOOL_CALLS_PER_MODEL_TURN
    assert attrs["tool_calls.rejected"] == 8 - MAX_TOOL_CALLS_PER_MODEL_TURN
    assert attrs["tool_calls.limit_exceeded"] is True
    assert attrs["tool_calls.limit"] == MAX_TOOL_CALLS_PER_MODEL_TURN


@pytest.mark.asyncio
async def test_enforce_tool_call_limit_event_within_cap(lifecycle_with_span):
    """3 calls in one turn: event fires with limit_exceeded=False."""
    lifecycle, parent_span = lifecycle_with_span
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    for _ in range(3):
        await lifecycle.wrap_tool_execute(
            ctx, call=None, tool_def=None, args=None, handler=_ok_handler
        )

    await lifecycle.after_node_run(ctx, node=_call_tools_node(), result=None)

    event = _find_event(parent_span, "tool_budget.enforce_tool_call_limit")
    assert event is not None, "enforce_tool_call_limit event must fire even for under-cap turns"
    attrs = event["attributes"]
    assert attrs["tool_calls.issued"] == 3
    assert attrs["tool_calls.limit_exceeded"] is False


@pytest.mark.asyncio
async def test_enforce_tool_call_limit_event_skipped_for_non_call_tools_node(lifecycle_with_span):
    """after_node_run must not emit the event when node is not a CallToolsNode."""
    lifecycle, parent_span = lifecycle_with_span
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    for _ in range(8):
        await lifecycle.wrap_tool_execute(
            ctx, call=None, tool_def=None, args=None, handler=_ok_handler
        )

    node = UserPromptNode(user_prompt="hi")
    await lifecycle.after_node_run(ctx, node=node, result=None)

    assert _find_event(parent_span, "tool_budget.enforce_tool_call_limit") is None, (
        f"Event must not fire for non-CallToolsNode; got events: {[e['name'] for e in parent_span['events']]}"
    )
