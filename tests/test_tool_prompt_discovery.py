"""Verify tool protocol guidance is present in assembled static instructions."""

from co_cli.config._core import settings
from co_cli.prompts._assembly import build_static_instructions


def test_static_instructions_contain_search_tools_guidance() -> None:
    """Assembled static instructions must reference search_tools for deferred discovery.

    Regression: if 04_tool_protocol.md loses its Deferred discovery section,
    the model gets no instruction to call search_tools and reverts to shell.
    """
    text = build_static_instructions(settings)
    assert "search_tools" in text


def test_static_instructions_contain_capabilities_check_guidance() -> None:
    """Assembled static instructions must steer capability questions to capabilities_check.

    Regression: if 04_tool_protocol.md loses its self-check section, the model
    answers capability questions from context instead of invoking the canonical
    self-check tool, and can overclaim or underclaim what is available.
    """
    text = build_static_instructions(settings)
    assert "capabilities_check" in text
    lowered = text.lower()
    assert any(cue in lowered for cue in ("capabilit", "available", "can you")), (
        "self-check guidance must reference at least one capability intent cue"
    )
