"""Tests for tool-result spill — both the per-call helper and the MCP lifecycle path.

Two layers:
  - spill_if_oversized(): direct helper API used by native tools.
  - CoToolLifecycle.after_tool_execute(): MCP-source results coerced through the helper;
    native results pass through (their tools call the helper themselves).
"""

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
from co_cli.tools.tool_io import (
    PERSISTED_OUTPUT_TAG,
    SPILL_THRESHOLD_CHARS,
    spill_if_oversized,
)

# ---------------------------------------------------------------------------
# spill_if_oversized — per-call helper
# ---------------------------------------------------------------------------


def test_no_spill_below_threshold(tmp_path: Path):
    """Content of 3_999 chars must be returned unchanged — no spill, no PERSISTED_OUTPUT_TAG."""
    content = "x" * 3_999
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell_exec")
    assert result == content
    assert PERSISTED_OUTPUT_TAG not in result


def test_spill_at_threshold(tmp_path: Path):
    """Content of 4_001 chars must trigger a spill and return a stub with PERSISTED_OUTPUT_TAG."""
    content = "x" * 4_001
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell_exec")
    assert PERSISTED_OUTPUT_TAG in result


def test_spill_large_content(tmp_path: Path):
    """Oversized content spills, returns a stub, and writes the original to disk verbatim."""
    content = "y" * 10_000
    tool_results_dir = tmp_path / "tool_results"
    result = spill_if_oversized(content, tool_results_dir, "file_read")
    assert PERSISTED_OUTPUT_TAG in result
    assert len(result) < len(content), "stub must be smaller than the original"
    spilled_files = list(tool_results_dir.glob("*.txt"))
    assert len(spilled_files) == 1, f"expected one persisted file, found: {spilled_files}"
    assert spilled_files[0].read_text(encoding="utf-8") == content


def test_stub_shape(tmp_path: Path):
    """Spilled stub carries the size preamble, the file_read retrieval hint, and start/end nav."""
    content = "z" * 5_000
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell_exec")
    assert "This tool result was too large" in result
    assert "file_read" in result, "stub must name the retrieval tool (not 'read_file')"
    assert "start_line" in result
    assert "end_line" in result


def test_force_spill_at_preview_size_unchanged(tmp_path: Path):
    """force=True at exactly TOOL_RESULT_PREVIEW_CHARS=1_500 chars returns content unchanged.

    The guard 'len(content) <= TOOL_RESULT_PREVIEW_CHARS' prevents spill when the
    resulting stub would be no smaller than the original content.
    """
    content = "x" * 1_500
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell_exec", force=True)
    assert result == content
    assert PERSISTED_OUTPUT_TAG not in result


def test_force_spill_above_preview_size_spills(tmp_path: Path):
    """force=True with 1_501 chars (just above TOOL_RESULT_PREVIEW_CHARS) must spill."""
    content = "x" * 1_501
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell_exec", force=True)
    assert PERSISTED_OUTPUT_TAG in result


# ---------------------------------------------------------------------------
# CoToolLifecycle.after_tool_execute — MCP spill enforcement
# ---------------------------------------------------------------------------


def _make_lifecycle_deps(tool_results_dir: Path) -> CoDeps:
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
    deps = _make_lifecycle_deps(tool_results_dir)
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
    deps = _make_lifecycle_deps(tool_results_dir)
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
    deps = _make_lifecycle_deps(tool_results_dir)
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
