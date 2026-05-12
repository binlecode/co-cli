"""Bundled skill manifest injection — declares bundled skills in the static system prompt."""

from pathlib import Path

from co_cli.context.manifests.skill_manifest import render_skill_manifest
from co_cli.skills.skill_types import SkillConfig


def test_manifest_renders_bundled_skills(tmp_path: Path) -> None:
    """Bundled skill is rendered as a <skill> entry inside <available_skills>."""
    skills_dir = tmp_path / "bundled"
    skills_dir.mkdir()
    (skills_dir / "doctor.md").write_text(
        "---\ndescription: Diagnose problems\n---\nBody.\n", encoding="utf-8"
    )
    user_skills_dir = tmp_path / "user"
    user_skills_dir.mkdir()

    skill_commands = {"doctor": SkillConfig(name="doctor", description="Diagnose problems")}
    out = render_skill_manifest(skill_commands, skills_dir, user_skills_dir)

    assert "<available_skills>" in out
    assert "</available_skills>" in out
    assert 'name="doctor"' in out
    assert 'description="Diagnose problems"' in out


def test_manifest_excludes_user_installed_skills(tmp_path: Path) -> None:
    """User-installed skills (only in user_skills_dir) do not appear in the manifest."""
    skills_dir = tmp_path / "bundled"
    skills_dir.mkdir()
    user_skills_dir = tmp_path / "user"
    user_skills_dir.mkdir()
    (user_skills_dir / "user-skill.md").write_text(
        "---\ndescription: A user skill\n---\nBody.\n", encoding="utf-8"
    )

    skill_commands = {"user-skill": SkillConfig(name="user-skill", description="A user skill")}
    out = render_skill_manifest(skill_commands, skills_dir, user_skills_dir)

    assert out == "", f"manifest must be empty when only user-installed skills exist; got: {out!r}"


def test_manifest_excludes_shadowed_bundled_skills(tmp_path: Path) -> None:
    """A bundled name shadowed by a same-named user file is excluded from the manifest."""
    skills_dir = tmp_path / "bundled"
    skills_dir.mkdir()
    (skills_dir / "doctor.md").write_text(
        "---\ndescription: Bundled doctor\n---\nBody.\n", encoding="utf-8"
    )
    user_skills_dir = tmp_path / "user"
    user_skills_dir.mkdir()
    (user_skills_dir / "doctor.md").write_text(
        "---\ndescription: User-shadowed doctor\n---\nBody.\n", encoding="utf-8"
    )

    skill_commands = {"doctor": SkillConfig(name="doctor", description="User-shadowed doctor")}
    out = render_skill_manifest(skill_commands, skills_dir, user_skills_dir)

    assert out == "", f"shadowed bundled skill must not appear in manifest; got: {out!r}"


def test_manifest_empty_when_no_skills(tmp_path: Path) -> None:
    """No bundled skills at all → returns empty string (not an empty XML block)."""
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

    skill_commands = {
        "special": SkillConfig(name="special", description='Quotes "inside" & <angle> brackets')
    }
    out = render_skill_manifest(skill_commands, skills_dir, user_skills_dir)

    assert "&quot;inside&quot;" in out
    assert "&amp;" in out
    assert "&lt;angle&gt;" in out
