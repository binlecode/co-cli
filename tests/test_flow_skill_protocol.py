"""Behavioral tests for skill protocol — prompt assembly and manifest coverage."""

from pathlib import Path

from tests._settings import SETTINGS


def test_protocol_file_in_assembled_static_prompt() -> None:
    """06_skill_protocol.md content must appear in the assembled static instructions."""
    from co_cli.context.assembly import build_static_instructions

    prompt = build_static_instructions(SETTINGS)
    assert "# Skill protocol" in prompt


def test_manifest_includes_skill_creator(tmp_path: Path) -> None:
    """<available_skills> manifest must include skill-creator."""
    from co_cli.context.manifests.skill_manifest import render_skill_manifest
    from co_cli.skills.loader import load_skills

    skills_dir = Path(__file__).parent.parent / "co_cli" / "skills"
    user_skills_dir = tmp_path / "user_skills"
    user_skills_dir.mkdir()
    skills = load_skills(skills_dir, SETTINGS, user_skills_dir=user_skills_dir)
    manifest = render_skill_manifest(skills, skills_dir, user_skills_dir)
    assert 'name="skill-creator"' in manifest, "skill-creator missing from manifest"


def test_protocol_has_background_review_section() -> None:
    """06_skill_protocol.md must contain ## Background review after ## Offer-to-save."""
    from co_cli.context.assembly import build_static_instructions

    prompt = build_static_instructions(SETTINGS)
    assert "## Background review" in prompt
    assert "session-end review" in prompt or "review agent" in prompt
    assert "/skills pin" in prompt
    assert "/skills curator restore" in prompt
