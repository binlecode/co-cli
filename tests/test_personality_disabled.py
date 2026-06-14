"""Disabled-personality assembly behavior — personality=None yields a rules-only prefix.

When ``personality`` is disabled (``None`` / JSON null / ``CO_PERSONALITY=none``), the base
instructions drop the soul seed, mindsets, critique, and canon — but the behavioral rules
(including ``02_safety`` and the tool protocol) still load unconditionally via
``build_rules_block()``. "Disable personality" must never mean "disable guardrails".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests._settings import SETTINGS

from co_cli.config.core import Settings
from co_cli.context.assembly import build_base_instructions, build_rules_block
from co_cli.personality.prompts.loader import load_soul_mindsets, load_soul_seed


def _validate(personality: object) -> Settings:
    """Validate a Settings with the given personality through the real field validator.

    Forces an empty env context so a real ``CO_PERSONALITY`` in the test environment
    cannot override the value under test (mirrors load_config's pre-flight at core.py).
    """
    return Settings.model_validate({"personality": personality}, context={"env": {}})


def test_validator_normalizes_disabled_values_to_none() -> None:
    """JSON null and the 'none' env-transport sentinel both resolve to None."""
    assert _validate(None).personality is None
    assert _validate("none").personality is None


def test_validator_accepts_discovered_role() -> None:
    assert _validate("tars").personality == "tars"


def test_validator_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        _validate("bogus")


def test_disabled_personality_assembles_rules_only() -> None:
    """personality=None drops seed + mindsets but keeps the full rules block."""
    config = SETTINGS.model_copy(update={"personality": None})

    prompt = build_base_instructions(config)

    assert load_soul_seed("tars") not in prompt
    assert "## Mindsets" not in prompt
    assert build_rules_block() in prompt


def test_enabled_personality_includes_seed_and_mindsets() -> None:
    """A valid role still assembles seed + mindsets + rules (default path unchanged)."""
    config = SETTINGS.model_copy(update={"personality": "tars"})

    prompt = build_base_instructions(config)

    assert load_soul_seed("tars") in prompt
    assert "## Mindsets" in prompt
    assert load_soul_mindsets("tars") in prompt
    assert build_rules_block() in prompt
