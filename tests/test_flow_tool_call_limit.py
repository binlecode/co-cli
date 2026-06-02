"""Tests for the per-model-request tool-call brake (L0 cap) — behavior + recorded events."""

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
from co_cli.tools.tool_call_limit import (
    MAX_TOOL_CALLS_PER_MODEL_REQUEST,
    TOOL_CAP_HARD_STOP_CONSECUTIVE,
    make_exceeded_payload,
)


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


@pytest.mark.asyncio
async def test_brake_allows_up_to_cap():
    """All calls up to the cap must reach the handler and return 'ok'."""
    lifecycle = CoToolLifecycle()
    deps = _make_deps()
    ctx = _ctx(deps, run_step=1)

    results = []
    for _ in range(MAX_TOOL_CALLS_PER_MODEL_REQUEST):
        result = await lifecycle.wrap_tool_execute(
            ctx,
            call=None,
            tool_def=None,
            args=None,
            handler=_ok_handler,
        )
        results.append(result)

    assert all(r == "ok" for r in results), (
        f"All {MAX_TOOL_CALLS_PER_MODEL_REQUEST} calls must reach handler; got: {results}"
    )


@pytest.mark.asyncio
async def test_brake_rejects_above_cap():
    """Calls past the cap (up to total 8) must be rejected; payloads must contain error and guidance."""
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

    # First MAX allowed
    assert all(r == "ok" for r in results[:MAX_TOOL_CALLS_PER_MODEL_REQUEST])

    # Remaining rejected
    for rejected in results[MAX_TOOL_CALLS_PER_MODEL_REQUEST:]:
        payload = json.loads(rejected)
        assert payload["error"] == "max_tool_calls_per_model_request_exceeded"
        assert "guidance" in payload
        guidance = payload["guidance"]
        assert str(MAX_TOOL_CALLS_PER_MODEL_REQUEST) in guidance
        # issued count (MAX+1 .. 8) must appear in guidance
        issued_in_guidance = any(
            str(i) in guidance for i in range(MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1, 9)
        )
        assert issued_in_guidance, f"guidance must contain the issued count; got: {guidance!r}"


@pytest.mark.asyncio
async def test_run_step_transition_resets_counter():
    """MAX calls at run_step=1 (all allowed), then MAX calls at run_step=2 (all allowed again)."""
    lifecycle = CoToolLifecycle()
    deps = _make_deps()

    # run_step=1: 6 calls, all should pass
    ctx1 = _ctx(deps, run_step=1)
    for _ in range(MAX_TOOL_CALLS_PER_MODEL_REQUEST):
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
    for _ in range(MAX_TOOL_CALLS_PER_MODEL_REQUEST):
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

    assert handler_call_count == MAX_TOOL_CALLS_PER_MODEL_REQUEST, (
        f"Expected exactly {MAX_TOOL_CALLS_PER_MODEL_REQUEST} handler calls, got {handler_call_count}"
    )
    ok_count = sum(1 for r in results if r == "ok")
    assert ok_count == MAX_TOOL_CALLS_PER_MODEL_REQUEST


def test_guidance_contains_interpolated_values():
    """make_exceeded_payload guidance must contain the cap value and the issued count."""
    issued = 9
    payload = make_exceeded_payload(issued)
    guidance = payload["guidance"]
    assert str(MAX_TOOL_CALLS_PER_MODEL_REQUEST) in guidance, (
        f"guidance must contain cap={MAX_TOOL_CALLS_PER_MODEL_REQUEST}; got: {guidance!r}"
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
    """8 calls in one turn: event fires with issued=8, allowed=MAX, rejected=8-MAX, limit_exceeded=True."""
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
    assert attrs["tool_calls.allowed"] == MAX_TOOL_CALLS_PER_MODEL_REQUEST
    assert attrs["tool_calls.rejected"] == 8 - MAX_TOOL_CALLS_PER_MODEL_REQUEST
    assert attrs["tool_calls.limit_exceeded"] is True
    assert attrs["tool_calls.limit"] == MAX_TOOL_CALLS_PER_MODEL_REQUEST


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


# ---------------------------------------------------------------------------
# Consecutive violation tracking (hard-stop accumulator)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consecutive_violations_accumulate_to_threshold(lifecycle_with_span):
    """Three successive over-cap CallToolsNodes must raise consecutive_tool_cap_violations to TOOL_CAP_HARD_STOP_CONSECUTIVE."""
    lifecycle, _ = lifecycle_with_span
    deps = _make_deps()

    for step in range(1, TOOL_CAP_HARD_STOP_CONSECUTIVE + 1):
        ctx = _ctx(deps, run_step=step)
        deps.runtime.tool_calls_in_model_request = MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1
        await lifecycle.after_node_run(ctx, node=_call_tools_node(), result=None)

    assert deps.runtime.consecutive_tool_cap_violations == TOOL_CAP_HARD_STOP_CONSECUTIVE


@pytest.mark.asyncio
async def test_consecutive_violations_reset_on_clean_node(lifecycle_with_span):
    """Two over-cap nodes followed by one under-cap node resets consecutive_tool_cap_violations to 0."""
    lifecycle, _ = lifecycle_with_span
    deps = _make_deps()

    for step in range(1, 3):
        ctx = _ctx(deps, run_step=step)
        deps.runtime.tool_calls_in_model_request = MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1
        await lifecycle.after_node_run(ctx, node=_call_tools_node(), result=None)

    assert deps.runtime.consecutive_tool_cap_violations == 2

    ctx_clean = _ctx(deps, run_step=3)
    deps.runtime.tool_calls_in_model_request = MAX_TOOL_CALLS_PER_MODEL_REQUEST
    await lifecycle.after_node_run(ctx_clean, node=_call_tools_node(), result=None)

    assert deps.runtime.consecutive_tool_cap_violations == 0
