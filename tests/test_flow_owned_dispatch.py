"""Behavioral tests for the owned loop's tool dispatch.

Asserts observable outcomes: the pre-fan-out cap executes within-cap calls and sheds the
rest with an exceeded payload, the ``co.tool.*`` span fires per executed call, MCP results
over threshold spill to a file, and DEFERRED tools stay out of the request's tool defs
until revealed via tool_view.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
from co_cli.agent.dispatch import dispatch_tools
from co_cli.agent.preflight import build_tool_defs
from co_cli.agent.turn_state import ToolCapState
from co_cli.config.tuning import (
    MAX_TOOL_CALLS_PER_MODEL_REQUEST,
    PERSISTED_OUTPUT_TAG,
    SPILL_THRESHOLD_CHARS,
)
from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.observability import tracing
from co_cli.tools.shell_backend import ShellBackend

CAP = MAX_TOOL_CALLS_PER_MODEL_REQUEST


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


def _info(name: str, *, source: ToolSourceEnum, visibility: VisibilityPolicyEnum) -> ToolInfo:
    return ToolInfo(
        name=name,
        description="test",
        is_approval_required=False,
        source=source,
        visibility=visibility,
        is_concurrent_safe=True,
    )


def _calls(name: str, n: int) -> list[ToolCallPart]:
    return [ToolCallPart(tool_name=name, args={}, tool_call_id=f"{name}-{i}") for i in range(n)]


@pytest.mark.asyncio
async def test_dispatch_executes_within_cap_and_sheds_the_rest(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    inner: FunctionToolset = FunctionToolset()

    async def echo() -> str:
        return "REAL_RESULT"

    inner.add_function(echo, requires_approval=False)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path / "tool-results",
        tool_catalog={
            "echo": _info(
                "echo", source=ToolSourceEnum.NATIVE, visibility=VisibilityPolicyEnum.ALWAYS
            )
        },
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )
    deps.toolset = inner

    issued = CAP + 2
    parts = await dispatch_tools(_calls("echo", issued), deps, cap_state=ToolCapState())

    assert len(parts) == issued
    real = [p for p in parts if p.content == "REAL_RESULT"]
    assert len(real) == CAP, "within-cap calls execute and return real results"
    shed = [p for p in parts if isinstance(p.content, str) and "max_tool_calls" in p.content]
    assert len(shed) == issued - CAP, "over-cap calls return the exceeded payload"
    for p in shed:
        payload = json.loads(p.content)
        assert payload["error"] == "max_tool_calls_per_model_request_exceeded"

    tool_spans = [r for r in _read_records(log) if r["kind"] == "tool"]
    assert len(tool_spans) == CAP, "one co.tool span per executed call"
    assert all(r["attributes"].get("co.tool.name") == "echo" for r in tool_spans)


@pytest.mark.asyncio
async def test_dispatch_marks_approved_call_tool_call_approved(tmp_path: Path) -> None:
    inner: FunctionToolset = FunctionToolset()

    async def probe(ctx: RunContext[CoDeps]) -> str:
        return "APPROVED" if ctx.tool_call_approved else "UNAPPROVED"

    inner.add_function(probe, requires_approval=False)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path / "tool-results",
        tool_catalog={
            "probe": _info(
                "probe", source=ToolSourceEnum.NATIVE, visibility=VisibilityPolicyEnum.ALWAYS
            )
        },
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )
    deps.toolset = inner

    calls = [
        ToolCallPart(tool_name="probe", args={}, tool_call_id="yes"),
        ToolCallPart(tool_name="probe", args={}, tool_call_id="no"),
    ]
    parts = await dispatch_tools(calls, deps, cap_state=ToolCapState(), approved_ids={"yes"})

    by_id = {p.tool_call_id: p.content for p in parts}
    assert by_id["yes"] == "APPROVED", "call in approved_ids runs with tool_call_approved=True"
    assert by_id["no"] == "UNAPPROVED", "call absent from approved_ids is unapproved"


@pytest.mark.asyncio
async def test_dispatch_spills_oversized_mcp_result_to_disk(tmp_path: Path) -> None:
    tool_results_dir = tmp_path / "tool-results"
    inner: FunctionToolset = FunctionToolset()
    oversized = "x" * (SPILL_THRESHOLD_CHARS + 1)

    async def mcp_big() -> str:
        return oversized

    inner.add_function(mcp_big, requires_approval=False)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tool_results_dir,
        tool_catalog={
            "mcp_big": _info(
                "mcp_big", source=ToolSourceEnum.MCP, visibility=VisibilityPolicyEnum.ALWAYS
            )
        },
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )
    deps.toolset = inner

    parts = await dispatch_tools(_calls("mcp_big", 1), deps, cap_state=ToolCapState())

    assert len(parts) == 1
    assert PERSISTED_OUTPUT_TAG in parts[0].content
    spilled = list(tool_results_dir.glob("*.txt"))
    assert len(spilled) == 1
    assert spilled[0].read_text(encoding="utf-8") == oversized


@pytest.mark.asyncio
async def test_build_tool_defs_hides_deferred_until_revealed(tmp_path: Path) -> None:
    native, catalog = build_native_toolset()
    deferred_name = next(
        name for name, info in catalog.items() if info.visibility == VisibilityPolicyEnum.DEFERRED
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path / "tool-results",
        tool_catalog=catalog,
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )
    deps.toolset = assemble_routing_toolset(native, [])

    before = {d.name for d in await build_tool_defs(deps)}
    assert deferred_name not in before, "DEFERRED tool hidden before reveal"

    deps.runtime.revealed_tools.add(deferred_name)
    after = {d.name for d in await build_tool_defs(deps)}
    assert deferred_name in after, "DEFERRED tool present after tool_view reveal"
