"""Tests for tool-result spill — the per-call helper and the owned dispatch path.

Two layers:
  - spill_if_oversized(): direct helper API used by native tools.
  - dispatch_tools(): MCP-source results (plain strings that bypass tool_output) are
    coerced through the spill helper after dispatch; native results pass through (their
    tools call the helper themselves). The over-threshold spill is also pinned in
    test_flow_owned_dispatch.py; these add the under-threshold pass-through and the
    native-not-coerced cases.
"""

from pathlib import Path

import pytest
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.dispatch import dispatch_tools
from co_cli.agent.turn_state import ToolCapState
from co_cli.config.tuning import PERSISTED_OUTPUT_TAG, SPILL_THRESHOLD_CHARS
from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.fileio.spill import spill_if_oversized
from co_cli.tools.shell_backend import ShellBackend

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


def test_force_spill_at_preview_size_unchanged(tmp_path: Path):
    """force=True at exactly SPILL_PREVIEW_CHARS=1_500 chars returns content unchanged.

    The guard 'len(content) <= SPILL_PREVIEW_CHARS' prevents spill when the
    resulting stub would be no smaller than the original content.
    """
    content = "x" * 1_500
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell_exec", force=True)
    assert result == content
    assert PERSISTED_OUTPUT_TAG not in result


def test_force_spill_above_preview_size_spills(tmp_path: Path):
    """force=True with 1_501 chars (just above SPILL_PREVIEW_CHARS) must spill."""
    content = "x" * 1_501
    result = spill_if_oversized(content, tmp_path / "tool_results", "shell_exec", force=True)
    assert PERSISTED_OUTPUT_TAG in result


# ---------------------------------------------------------------------------
# dispatch_tools — MCP spill enforcement on the owned path
# ---------------------------------------------------------------------------


def _info(name: str, *, source: ToolSourceEnum) -> ToolInfo:
    return ToolInfo(
        name=name,
        description="test",
        is_approval_required=False,
        source=source,
        visibility=VisibilityPolicyEnum.ALWAYS,
        is_concurrent_safe=True,
    )


def _make_deps(
    tool_results_dir: Path, catalog: dict[str, ToolInfo], inner: FunctionToolset
) -> CoDeps:
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tool_results_dir,
        tool_catalog=catalog,
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )
    deps.toolset = inner
    return deps


async def _dispatch_emit(deps: CoDeps, tool_name: str) -> str:
    parts = await dispatch_tools(
        [ToolCallPart(tool_name=tool_name, args={}, tool_call_id="c1")],
        deps,
        cap_state=ToolCapState(),
    )
    return parts[0].content


@pytest.mark.asyncio
async def test_mcp_result_under_threshold_passes_through(tmp_path: Path):
    tool_results_dir = tmp_path / "tool_results"
    small = "x" * (SPILL_THRESHOLD_CHARS - 1)
    inner: FunctionToolset = FunctionToolset()

    async def mcp_small() -> str:
        return small

    inner.add_function(mcp_small, requires_approval=False)
    deps = _make_deps(
        tool_results_dir, {"mcp_small": _info("mcp_small", source=ToolSourceEnum.MCP)}, inner
    )

    result = await _dispatch_emit(deps, "mcp_small")

    assert result == small
    assert not tool_results_dir.exists()


@pytest.mark.asyncio
async def test_native_result_over_threshold_not_coerced_by_dispatch(tmp_path: Path):
    tool_results_dir = tmp_path / "tool_results"
    oversized = "x" * (SPILL_THRESHOLD_CHARS + 1)
    inner: FunctionToolset = FunctionToolset()

    async def native_big() -> str:
        return oversized

    inner.add_function(native_big, requires_approval=False)
    deps = _make_deps(
        tool_results_dir, {"native_big": _info("native_big", source=ToolSourceEnum.NATIVE)}, inner
    )

    result = await _dispatch_emit(deps, "native_big")

    # dispatch coerces only MCP-source results; native tools spill themselves.
    assert result == oversized
    assert not tool_results_dir.exists()
