"""Section-order gate for static instruction assembly."""

from co_cli.config._core import settings
from co_cli.context._history import COMPACTABLE_KEEP_RECENT
from co_cli.prompts._assembly import RECENCY_CLEARING_ADVISORY, build_static_instructions

# Base config — personality overridden per test.
_BASE_CONFIG = settings


def test_section_order_finch() -> None:
    """All static instruction sections appear in the required order for finch personality.

    Required order: soul seed < first rule < soul examples < critique.
    """
    config = _BASE_CONFIG.model_copy(update={"personality": "finch"})
    prompt = build_static_instructions(config=config)

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


def test_section_order_no_personality() -> None:
    """Assembly without personality still produces a non-empty prompt (rules only)."""
    config = _BASE_CONFIG.model_copy(update={"personality": None})
    prompt = build_static_instructions(config=config)

    rule_anchor = "## Relationship"
    assert rule_anchor in prompt, "Rule content missing when personality is None"


def test_git_safety_in_static_instructions() -> None:
    """Git safety guidance (force-push, hook bypass, amend) appears in assembled prompt."""
    config = _BASE_CONFIG.model_copy(update={"personality": None})
    prompt = build_static_instructions(config=config)

    assert "force-push" in prompt, "Git force-push guidance missing from static instructions"
    assert "no-verify" in prompt, "Git hook-skip guidance missing from static instructions"
    assert "hook" in prompt, "Git hook failure guidance missing from static instructions"


def test_memory_ephemeral_in_static_instructions() -> None:
    """Ephemeral session state exclusion appears in assembled prompt."""
    config = _BASE_CONFIG.model_copy(update={"personality": None})
    prompt = build_static_instructions(config=config)

    assert "ephemeral" in prompt, "Ephemeral memory exclusion missing from static instructions"
    assert "session" in prompt, "Session context guidance missing from static instructions"


def test_cutoff_awareness_in_static_instructions() -> None:
    """Knowledge cutoff awareness appears in assembled prompt."""
    config = _BASE_CONFIG.model_copy(update={"personality": None})
    prompt = build_static_instructions(config=config)

    assert "cutoff" in prompt, "Knowledge cutoff guidance missing from static instructions"
    assert "stale" in prompt, "Stale data guidance missing from static instructions"


def test_recency_clearing_advisory_in_cacheable_prefix() -> None:
    """Gap G: recency-clearing advisory is present in the static (cacheable) prefix.

    The static instructions slice is the cacheable prefix sent to the provider.
    The advisory describes the ``[tool result cleared…]`` placeholders that
    appear after ``truncate_tool_results`` runs — the model must have a mental
    model for them. Static per process (no per-turn interpolation) so it does
    not break prompt cache hits.
    """
    config = _BASE_CONFIG.model_copy(update={"personality": None})
    prompt = build_static_instructions(config=config)
    assert RECENCY_CLEARING_ADVISORY in prompt
    # The advisory must reference the actual COMPACTABLE_KEEP_RECENT value
    assert str(COMPACTABLE_KEEP_RECENT) in RECENCY_CLEARING_ADVISORY
    # Key phrases that signal the model's expected mental model
    assert "cleared" in prompt.lower()
    assert "recent" in prompt.lower()
