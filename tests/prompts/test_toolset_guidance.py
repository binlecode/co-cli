"""Tests for build_toolset_guidance — conditional tool-specific guidance assembly."""

from co_cli.context.guidance import build_toolset_guidance
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum


def _make_tool(name: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} tool",
        approval=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
    )


def test_memory_search_present_returns_memory_guidance() -> None:
    """When memory_search is in tool_index, memory guidance is emitted."""
    index = {"memory_search": _make_tool("memory_search")}
    result = build_toolset_guidance(index)
    assert "at most one broader retry" in result
    # capabilities text must not bleed in
    assert "capabilities_check" not in result


def test_capabilities_check_present_returns_capabilities_guidance() -> None:
    """When capabilities_check is in tool_index, capabilities guidance is emitted."""
    index = {"capabilities_check": _make_tool("capabilities_check")}
    result = build_toolset_guidance(index)
    assert "capabilities_check" in result
    # memory retry text must not bleed in
    assert "at most one broader retry" not in result


def test_empty_tool_index_returns_empty_string() -> None:
    """When no tools are present, no guidance noise is emitted."""
    assert build_toolset_guidance({}) == ""


def test_both_tools_present_returns_both_guidance_blocks() -> None:
    """When both tools are present, both guidance blocks appear."""
    index = {
        "memory_search": _make_tool("memory_search"),
        "capabilities_check": _make_tool("capabilities_check"),
    }
    result = build_toolset_guidance(index)
    assert "at most one broader retry" in result
    assert "capabilities_check" in result


def test_unrelated_tool_only_returns_empty() -> None:
    """Tools not gated by build_toolset_guidance produce no output."""
    index = {"shell": _make_tool("shell"), "file_read": _make_tool("file_read")}
    assert build_toolset_guidance(index) == ""
