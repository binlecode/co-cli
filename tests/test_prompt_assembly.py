"""Section-order gate for static instruction assembly."""

from pathlib import Path

from co_cli.config._core import settings
from co_cli.prompts._assembly import build_static_instructions

# Base config — personality overridden per test.
_BASE_CONFIG = settings


def test_section_order_finch(tmp_path: Path) -> None:
    """All static instruction sections appear in the required order for finch personality.

    Required order: soul seed < first rule < soul examples < critique.
    Counter-steering is verified when present (no current model quirk file defines it).
    """
    config = _BASE_CONFIG.model_copy(update={"personality": "finch"})
    prompt = build_static_instructions(provider="gemini", model_name="", config=config)

    # Anchor texts from the finch personality assets and rule files
    seed_anchor = "You are Finch"
    rule_anchor = "## Relationship"  # first line of 01_identity.md
    examples_anchor = "## Response patterns"  # first line of finch/examples.md
    critique_anchor = "## Review lens"  # wrapper injected around finch/critique.md

    assert seed_anchor in prompt, "Soul seed missing from assembled instructions"
    assert rule_anchor in prompt, "Rule content missing from assembled instructions"
    assert examples_anchor in prompt, "Soul examples missing from assembled instructions"
    assert critique_anchor in prompt, "Critique missing from assembled instructions"

    assert prompt.index(seed_anchor) < prompt.index(rule_anchor), (
        "Soul seed must appear before rules"
    )
    assert prompt.index(rule_anchor) < prompt.index(examples_anchor), (
        "Rules must appear before soul examples"
    )
    assert prompt.index(examples_anchor) < prompt.index(critique_anchor), (
        "Soul examples must appear before critique"
    )

    # Counter-steering: verify ordering when present
    counter_steering_anchor = "## Model-Specific Guidance"
    if counter_steering_anchor in prompt:
        assert prompt.index(examples_anchor) < prompt.index(counter_steering_anchor), (
            "Soul examples must appear before counter-steering"
        )
        assert prompt.index(counter_steering_anchor) < prompt.index(critique_anchor), (
            "Counter-steering must appear before critique"
        )


def test_section_order_no_personality(tmp_path: Path) -> None:
    """Assembly without personality still produces a non-empty prompt (rules only)."""
    config = _BASE_CONFIG.model_copy(update={"personality": None})
    prompt = build_static_instructions(provider="gemini", model_name="", config=config)

    rule_anchor = "## Relationship"
    assert rule_anchor in prompt, "Rule content missing when personality is None"
