"""Tests for per-tool max_result_size wiring in tool_output() and persist_if_oversized()."""

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import test_settings

from co_cli.agent import build_agent
from co_cli.config._core import settings
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_output import tool_output, tool_output_raw
from co_cli.tools.tool_result_storage import (
    PERSISTED_OUTPUT_TAG,
    TOOL_RESULT_MAX_SIZE,
    persist_if_oversized,
)

_CONFIG = settings
_AGENT = build_agent(config=_CONFIG)


def _make_ctx_with_index(
    tmp_path: Path,
    tool_name: str,
    max_result_size: int,
) -> RunContext[CoDeps]:
    """Build a RunContext with a tool_index entry for the given tool."""
    info = ToolInfo(
        name=tool_name,
        description="test tool",
        approval=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
        max_result_size=max_result_size,
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        tool_results_dir=tmp_path / "tool-results",
        tool_index={tool_name: info},
    )
    return RunContext(
        deps=deps,
        model=_AGENT.model,
        usage=RunUsage(),
        tool_name=tool_name,
    )


def test_tool_output_uses_per_tool_threshold(tmp_path: Path) -> None:
    """tool_output() persists when content exceeds the per-tool max_result_size."""
    ctx = _make_ctx_with_index(tmp_path, "test_tool", max_result_size=100)
    content = "x" * 150
    result = tool_output(content, ctx=ctx)
    assert PERSISTED_OUTPUT_TAG in result.return_value


def test_tool_output_under_per_tool_threshold(tmp_path: Path) -> None:
    """tool_output() does not persist when content is under the per-tool max_result_size."""
    ctx = _make_ctx_with_index(tmp_path, "test_tool", max_result_size=100)
    content = "x" * 50
    result = tool_output(content, ctx=ctx)
    assert result.return_value == content


def test_tool_output_raw_returns_unchanged() -> None:
    """tool_output_raw() returns content unchanged regardless of size (no ctx)."""
    content = "x" * (TOOL_RESULT_MAX_SIZE + 1000)
    result = tool_output_raw(content)
    assert result.return_value == content


def test_tool_output_falls_back_when_tool_not_in_index(tmp_path: Path) -> None:
    """tool_output() falls back to global threshold when tool_name is not in tool_index."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=test_settings(),
        tool_results_dir=tmp_path / "tool-results",
        tool_index={},
    )
    ctx = RunContext(
        deps=deps,
        model=_AGENT.model,
        usage=RunUsage(),
        tool_name="unknown_tool",
    )
    # Content under global threshold → no persistence
    content = "x" * (TOOL_RESULT_MAX_SIZE - 100)
    result = tool_output(content, ctx=ctx)
    assert result.return_value == content


def test_persist_if_oversized_with_explicit_max_size(tmp_path: Path) -> None:
    """persist_if_oversized() respects an explicit max_size argument."""
    content = "y" * 200
    result = persist_if_oversized(content, tmp_path, "test_tool", max_size=100)
    assert PERSISTED_OUTPUT_TAG in result


def test_persist_if_oversized_default_max_size(tmp_path: Path) -> None:
    """persist_if_oversized() uses TOOL_RESULT_MAX_SIZE as default when max_size not given."""
    # Content under default threshold → returned unchanged
    content = "z" * 100
    result = persist_if_oversized(content, tmp_path, "test_tool")
    assert result == content
