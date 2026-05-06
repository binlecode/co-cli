"""Tests for the per-model-turn tool-call brake (L0 cap)."""

import asyncio
import json

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent._tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN, make_exceeded_payload
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.lifecycle import CoToolLifecycle
from co_cli.tools.shell_backend import ShellBackend


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
    )


def _ctx(deps: CoDeps, run_step: int = 1) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage(), run_step=run_step)


async def _ok_handler(args) -> str:
    return "ok"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_constant_pinned():
    """MAX_TOOL_CALLS_PER_MODEL_TURN must be exactly 6."""
    assert MAX_TOOL_CALLS_PER_MODEL_TURN == 6


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
