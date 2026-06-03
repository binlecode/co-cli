"""Tests for prompt assembly — toolset guidance emission."""


def test_toolset_guidance_memory_section_emitted_when_tool_present() -> None:
    """build_toolset_guidance must emit the memory retry rule when memory_search is in the index."""
    from co_cli.context.guidance import build_toolset_guidance
    from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

    tool_index = {
        "memory_search": ToolInfo(
            name="memory_search",
            description="search knowledge artifacts",
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.ALWAYS,
        ),
    }
    guidance = build_toolset_guidance(tool_index)
    assert "at most one broader retry" in guidance


def test_toolset_guidance_memory_section_absent_without_tool() -> None:
    """build_toolset_guidance must not emit memory guidance when neither search tool is present."""
    from co_cli.context.guidance import build_toolset_guidance

    guidance = build_toolset_guidance({})
    assert "at most one broader retry" not in guidance
