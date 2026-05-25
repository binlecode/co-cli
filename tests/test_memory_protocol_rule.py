"""07_memory_protocol.md must be wired into static prompt assembly.

Asserts the rule renders in the assembled prompt (proves the file is collected by
the rules loader, not merely present on disk) and that the migrated subsections
no longer appear under 04_tool_protocol.md's body — a cross-reference line is fine.
"""

from tests._settings import SETTINGS

from co_cli.context.assembly import build_static_instructions


def test_memory_protocol_rule_rendered_in_assembled_instructions() -> None:
    prompt = build_static_instructions(SETTINGS)
    assert "# Memory protocol" in prompt
    assert "## Curation" in prompt
    assert "Promotion." in prompt
    assert "Correction." in prompt
    assert "Drift." in prompt


def test_tool_protocol_keeps_only_cross_reference_to_memory_protocol() -> None:
    prompt = build_static_instructions(SETTINGS)
    assert "See `07_memory_protocol.md`" in prompt, (
        "04_tool_protocol.md must leave a cross-reference after the migration"
    )
    assert "### Recall" not in prompt, (
        "### Recall (H3) must not appear — Memory subsections moved to 07 as H2"
    )
    assert "### Explicit saves" not in prompt, (
        "### Explicit saves (H3) must not appear — moved to 07 as H2"
    )
