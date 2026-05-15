"""Tests for the co_cli bundled skill library — load + lint + manifest coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from co_cli.context.manifests.skill_manifest import render_skill_manifest
from co_cli.skills._lint import lint_skill
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


@pytest.mark.parametrize("name", sorted(_BUNDLED_NAMES))
def test_bundled_skill_lints_clean(name: str) -> None:
    """Assertion 3: each bundled skill produces no lint findings."""
    path = _SKILLS_DIR / f"{name}.md"
    content = path.read_text(encoding="utf-8")
    findings = lint_skill(content, path)
    assert findings == [], f"Skill '{name}' has lint findings: {findings}"


def test_manifest_renders_six_bundled_entries(tmp_path: Path) -> None:
    """Assertion 5: manifest renders 6 <skill> entries for the full bundled set."""
    user_skills_dir = tmp_path / "user_skills"
    user_skills_dir.mkdir()
    skills = load_skills(_SKILLS_DIR)
    manifest = render_skill_manifest(skills, _SKILLS_DIR, user_skills_dir)
    entries = [line for line in manifest.splitlines() if "<skill name=" in line]
    bundled_entries = [e for e in entries if any(name in e for name in _BUNDLED_NAMES)]
    assert len(bundled_entries) == 6, (
        f"Expected 6 bundled skill entries in manifest, got {len(bundled_entries)}:\n{manifest}"
    )
