"""Skill manifest injection — declares all discoverable skills in the static system prompt."""

from pathlib import Path

from co_cli.context.manifests.skill_manifest import render_skill_manifest
from co_cli.skills.skill_types import SkillInfo


def test_manifest_renders_bundled_skills(tmp_path: Path) -> None:
    """A catalog skill is rendered as the exact <skill .../> line inside the block.

    Failure mode: a render that dropped the name, description, or self-closing
    shape would leave the agent with an unparseable or content-free manifest.
    """
    skill_catalog = {"doctor": SkillInfo(name="doctor", description="Diagnose problems")}
    out = render_skill_manifest(skill_catalog, tmp_path, tmp_path)

    assert "<available_skills>" in out
    assert "</available_skills>" in out
    assert '<skill name="doctor" description="Diagnose problems" />' in out


def test_manifest_empty_when_no_skills(tmp_path: Path) -> None:
    """No skills at all → returns empty string (not an empty XML block)."""
    out = render_skill_manifest({}, tmp_path, tmp_path)
    assert out == "", f"empty skill set must return empty string; got: {out!r}"


def test_manifest_escapes_special_chars(tmp_path: Path) -> None:
    """Skill descriptions containing XML-special chars are escaped so the block stays parseable."""
    skill_catalog = {
        "special": SkillInfo(name="special", description='Quotes "inside" & <angle> brackets')
    }
    out = render_skill_manifest(skill_catalog, tmp_path, tmp_path)

    assert "&quot;inside&quot;" in out
    assert "&amp;" in out
    assert "&lt;angle&gt;" in out
