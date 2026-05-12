"""Behavioral tests for the skill protocol rule and tool docstring discipline."""

import inspect
from pathlib import Path

from tests._settings import SETTINGS

_RULES_DIR = Path(__file__).parent.parent / "co_cli" / "context" / "rules"
_PROTOCOL_FILE = _RULES_DIR / "06_skill_protocol.md"

_FIVE_REFLEXES = ["## Discovery", "## Use", "## Drift", "## Create", "## Offer-to-save"]
_TIER_SENTENCE = "Skills sit at a different operational tier than memory."


def test_protocol_file_exists_with_06_prefix() -> None:
    """06_skill_protocol.md must exist in co_cli/context/rules/ with the 06_ prefix."""
    assert _PROTOCOL_FILE.exists(), f"Missing: {_PROTOCOL_FILE}"
    assert _PROTOCOL_FILE.name.startswith("06_")


def test_protocol_file_in_assembled_static_prompt() -> None:
    """06_skill_protocol.md content must appear in the assembled static instructions."""
    from co_cli.context.assembly import build_static_instructions

    prompt = build_static_instructions(SETTINGS)
    assert "# Skill protocol" in prompt


def test_protocol_file_has_five_reflex_sections() -> None:
    """All five ## reflex section headers must be present in the protocol file."""
    content = _PROTOCOL_FILE.read_text(encoding="utf-8")
    for header in _FIVE_REFLEXES:
        assert header in content, f"Missing reflex section: {header!r}"


def test_protocol_file_has_tier_distinction_sentence() -> None:
    """The tier-distinction opening sentence must be present in the protocol file."""
    content = _PROTOCOL_FILE.read_text(encoding="utf-8")
    assert _TIER_SENTENCE in content


def test_skill_manage_docstring_has_creation_trigger() -> None:
    """skill_manage docstring must contain the 3+ coherent steps creation trigger."""
    from co_cli.tools.system.skills import skill_manage

    doc = inspect.getdoc(skill_manage) or ""
    assert "3+ coherent steps" in doc


def test_skill_view_docstring_has_read_before_write() -> None:
    """skill_view docstring must contain the read-before-write reminder."""
    from co_cli.tools.system.skills import skill_view

    doc = inspect.getdoc(skill_view) or ""
    assert "Don't edit blind." in doc


def test_skill_search_docstring_has_dedup_guard() -> None:
    """skill_search docstring must contain the dedup guard (search before creating)."""
    from co_cli.tools.system.skills import skill_search

    doc = inspect.getdoc(skill_search) or ""
    assert "skill_manage(action='create')" in doc
