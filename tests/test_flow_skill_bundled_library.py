"""Tests for the co_cli bundled skill library — load + lint + manifest coverage."""

from __future__ import annotations

from pathlib import Path

from co_cli.skills.loader import load_skills

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "co_cli" / "skills"
_BUNDLED_NAMES = {
    "doctor",
    "review",
    "plan",
    "triage",
    "refactor",
    "skill-creator",
}


def test_all_bundled_skills_load() -> None:
    """Assertion 1: all 6 bundled skills load successfully."""
    skills = load_skills(_SKILLS_DIR)
    loaded_names = set(skills.keys())
    missing = _BUNDLED_NAMES - loaded_names
    assert not missing, f"Missing bundled skills: {missing}"
