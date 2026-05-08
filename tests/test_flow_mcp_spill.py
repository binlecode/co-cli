"""Tests for CoToolLifecycle.after_tool_execute — MCP result spill enforcement."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.tools.lifecycle import CoToolLifecycle
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG, SPILL_THRESHOLD_CHARS


def _make_deps(tool_results_dir: Path) -> CoDeps:
    mcp_info = ToolInfo(
        name="mcp_test_tool",
        description="test",
        approval=False,
        source=ToolSourceEnum.MCP,
        visibility=VisibilityPolicyEnum.DEFERRED,
    )
    native_info = ToolInfo(
        name="native_test_tool",
        description="test",
        approval=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
    )
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tool_results_dir,
        tool_index={"mcp_test_tool": mcp_info, "native_test_tool": native_info},
    )


def _ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage(), run_step=1)


def _call(tool_name: str) -> ToolCallPart:
    return ToolCallPart(tool_name=tool_name, args={}, tool_call_id="c1")


@pytest.mark.asyncio
async def test_mcp_result_over_threshold_is_spilled(tmp_path: Path):
    tool_results_dir = tmp_path / "tool_results"
    deps = _make_deps(tool_results_dir)
    lc = CoToolLifecycle()
    oversized = "x" * (SPILL_THRESHOLD_CHARS + 1)

    result = await lc.after_tool_execute(
        _ctx(deps),
        call=_call("mcp_test_tool"),
        tool_def=None,
        args={},
        result=oversized,
    )

    assert PERSISTED_OUTPUT_TAG in result
    spilled_files = list(tool_results_dir.glob("*.txt"))
    assert len(spilled_files) == 1


@pytest.mark.asyncio
async def test_mcp_result_under_threshold_passes_through(tmp_path: Path):
    tool_results_dir = tmp_path / "tool_results"
    deps = _make_deps(tool_results_dir)
    lc = CoToolLifecycle()
    small = "x" * (SPILL_THRESHOLD_CHARS - 1)

    result = await lc.after_tool_execute(
        _ctx(deps),
        call=_call("mcp_test_tool"),
        tool_def=None,
        args={},
        result=small,
    )

    assert result == small
    assert not tool_results_dir.exists()


@pytest.mark.asyncio
async def test_native_result_over_threshold_not_coerced_by_lifecycle(tmp_path: Path):
    tool_results_dir = tmp_path / "tool_results"
    deps = _make_deps(tool_results_dir)
    lc = CoToolLifecycle()
    oversized = "x" * (SPILL_THRESHOLD_CHARS + 1)

    result = await lc.after_tool_execute(
        _ctx(deps),
        call=_call("native_test_tool"),
        tool_def=None,
        args={},
        result=oversized,
    )

    # lifecycle defers to tool_output() for native tools — no spill here
    assert result == oversized
    assert not tool_results_dir.exists()
