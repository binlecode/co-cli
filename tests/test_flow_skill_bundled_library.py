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
    "documents",
    "office",
}


def test_all_bundled_skills_load() -> None:
    """All bundled skills load from <name>/SKILL.md with parsed body + description.

    Failure mode: the folder-layout glob regressing (or a SKILL.md the loader
    discovers but fails to parse) surfaces as a missing name or an empty
    body/description — name-membership alone would not catch the empty-parse case.
    """
    skills = load_skills(_SKILLS_DIR)
    loaded_names = set(skills.keys())
    missing = _BUNDLED_NAMES - loaded_names
    assert not missing, f"Missing bundled skills: {missing}"
    for name in _BUNDLED_NAMES:
        skill = skills[name]
        assert skill.body.strip(), f"{name} loaded with empty body"
        assert skill.description.strip(), f"{name} loaded with empty description"
