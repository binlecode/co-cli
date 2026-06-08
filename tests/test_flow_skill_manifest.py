"""Skill manifest injection — declares all discoverable skills in the static system prompt."""

from pathlib import Path

from co_cli.context.manifests.skill_manifest import render_skill_manifest
from co_cli.skills.skill_types import SkillInfo


def test_manifest_renders_bundled_skills(tmp_path: Path) -> None:
    """Bundled skill is rendered as a <skill> entry inside <available_skills>."""
    skills_dir = tmp_path / "bundled"
    skills_dir.mkdir()
    (skills_dir / "doctor.md").write_text(
        "---\ndescription: Diagnose problems\n---\nBody.\n", encoding="utf-8"
    )
    user_skills_dir = tmp_path / "user"
    user_skills_dir.mkdir()

    skill_catalog = {"doctor": SkillInfo(name="doctor", description="Diagnose problems")}
    out = render_skill_manifest(skill_catalog, skills_dir, user_skills_dir)

    assert "<available_skills>" in out
    assert "</available_skills>" in out
    assert 'name="doctor"' in out
    assert 'description="Diagnose problems"' in out


def test_manifest_empty_when_no_skills(tmp_path: Path) -> None:
    """No skills at all → returns empty string (not an empty XML block)."""
    skills_dir = tmp_path / "bundled"
    skills_dir.mkdir()
    user_skills_dir = tmp_path / "user"
    user_skills_dir.mkdir()

    out = render_skill_manifest({}, skills_dir, user_skills_dir)
    assert out == "", f"empty skill set must return empty string; got: {out!r}"


def test_manifest_escapes_special_chars(tmp_path: Path) -> None:
    """Skill descriptions containing XML-special chars are escaped so the block stays parseable."""
    skills_dir = tmp_path / "bundled"
    skills_dir.mkdir()
    (skills_dir / "special.md").write_text(
        "---\ndescription: ignored\n---\nBody.\n", encoding="utf-8"
    )
    user_skills_dir = tmp_path / "user"
    user_skills_dir.mkdir()

    skill_catalog = {
        "special": SkillInfo(name="special", description='Quotes "inside" & <angle> brackets')
    }
    out = render_skill_manifest(skill_catalog, skills_dir, user_skills_dir)

    assert "&quot;inside&quot;" in out
    assert "&amp;" in out
    assert "&lt;angle&gt;" in out
