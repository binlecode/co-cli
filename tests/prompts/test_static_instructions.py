"""Tests for build_static_instructions — static system prompt assembly."""

from tests._settings import make_settings

from co_cli.context.assembly import build_static_instructions


def test_static_instructions_contains_phase1_rules() -> None:
    """Phase-1 rule edits appear in assembled static instructions."""
    config = make_settings().model_copy(update={"personality": None})
    result = build_static_instructions(config)

    # P1.1 — don't-stop-at-plan pressure in 05_workflow.md
    assert "Only stop at a plan when the user explicitly" in result, (
        "missing don't-stop-at-plan text"
    )
    # P1.4 — deterministic-state examples in 03_reasoning.md
    assert "git state" in result, "missing deterministic-state examples"
    # P1.5 — shell section in 04_tool_protocol.md
    assert "--non-interactive" in result, "missing shell non-interactive guidance"
    # P1.2 — obvious-default guidance in 03_reasoning.md
    assert "obvious default interpretation" in result, "missing obvious-default guidance"
    # P2: "at most one broader retry" moved to build_toolset_guidance (see test_toolset_guidance.py)


def test_static_assembly_includes_toolset_and_category_guidance() -> None:
    """build_toolset_guidance and build_category_awareness_prompt assemble into static instructions."""
    from co_cli.context.guidance import build_toolset_guidance
    from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
    from co_cli.tools.deferred_prompt import build_category_awareness_prompt

    config = make_settings().model_copy(update={"personality": None})
    base = build_static_instructions(config)

    tool_index = {
        "memory_search": ToolInfo(
            name="memory_search",
            description="search",
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.ALWAYS,
        ),
        "file_write": ToolInfo(
            name="file_write",
            description="write file",
            approval=True,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.DEFERRED,
        ),
    }
    guidance = build_toolset_guidance(tool_index)
    category_hint = build_category_awareness_prompt(tool_index)

    static_parts = [p for p in [base, guidance, category_hint] if p]
    combined = "\n\n".join(static_parts)

    # memory guidance gated on memory_search
    assert "at most one broader retry" in combined
    # category hint fires for deferred file_write
    assert "file editing" in combined


def test_block0_critique_is_last() -> None:
    """Critique (Review lens) is the last section in Block 0 when personality is configured."""
    from co_cli.context.guidance import build_toolset_guidance
    from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
    from co_cli.personality.prompts.loader import load_soul_critique
    from co_cli.tools.deferred_prompt import build_category_awareness_prompt

    config = make_settings().model_copy(update={"personality": "finch"})
    critique_text = load_soul_critique(config.personality)
    assert critique_text, "finch personality must have a critique for this test to be meaningful"

    tool_index = {
        "memory_search": ToolInfo(
            name="memory_search",
            description="search",
            approval=False,
            source=ToolSourceEnum.NATIVE,
            visibility=VisibilityPolicyEnum.ALWAYS,
        ),
    }
    base = build_static_instructions(config)
    guidance = build_toolset_guidance(tool_index)
    category_hint = build_category_awareness_prompt(tool_index)

    static_parts = [p for p in [base, guidance, category_hint] if p]
    if critique_text:
        static_parts.append(f"## Review lens\n\n{critique_text}")
    combined = "\n\n".join(static_parts)

    assert combined.endswith(f"## Review lens\n\n{critique_text}"), (
        "critique must be the last section of Block 0; something follows it"
    )
