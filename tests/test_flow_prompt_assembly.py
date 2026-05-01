"""Tests for prompt assembly — static instructions and toolset guidance emission."""

from tests._settings import SETTINGS

from co_cli.context.assembly import build_static_instructions


def test_static_instructions_contains_phase1_rules() -> None:
    """Assembled static instructions must include the don't-stop-at-plan workflow rule."""
    result = build_static_instructions(SETTINGS)
    assert "Only stop at a plan when the user explicitly" in result


def test_toolset_guidance_memory_section_emitted_when_tool_present() -> None:
    """build_toolset_guidance must emit the memory retry rule when memory_search is in the index."""
    from co_cli.context.guidance import build_toolset_guidance
    from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

    tool_index = {
        "memory_search": ToolInfo(
            name="memory_search",
            description="search",
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.ALWAYS,
        ),
    }
    guidance = build_toolset_guidance(tool_index)
    assert "at most one broader retry" in guidance


def test_toolset_guidance_memory_section_absent_without_tool() -> None:
    """build_toolset_guidance must not emit memory guidance when memory_search is absent."""
    from co_cli.context.guidance import build_toolset_guidance

    guidance = build_toolset_guidance({})
    assert "at most one broader retry" not in guidance
