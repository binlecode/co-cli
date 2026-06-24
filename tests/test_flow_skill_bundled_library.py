"""Tests for the co_cli bundled skill library — load + lint + manifest coverage."""

from __future__ import annotations

from pathlib import Path

from co_cli.skills.lint import lint_bundled_extras, lint_skill
from co_cli.skills.loader import load_skills

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "co_cli" / "skills"
_BUNDLED_NAMES = {
    "doctor",
    "plan",
    "skill-creator",
    "pdf",
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


def test_all_bundled_skills_lint_clean() -> None:
    """Every shipped bundled SKILL.md passes the authoring lint gate.

    R1-R3 (frontmatter present, description present and within budget, H1 title)
    and B1 (no TODO/FIXME/XXX markers) are hard: the reference library must not
    ship a structural lint violation or an in-progress marker. R4 (body-size
    soft warning at 8000 chars) is advisory and intentionally not asserted here.
    """
    for name in _BUNDLED_NAMES:
        content = (_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        hard_findings = [f for f in lint_skill(content) if f.rule != "R4"]
        assert not hard_findings, f"{name} has R1-R3 lint findings: {hard_findings}"
        marker_findings = lint_bundled_extras(content)
        assert not marker_findings, f"{name} has B1 marker findings: {marker_findings}"
