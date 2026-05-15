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
    assert "after every ~5 tool calls" in prompt


def test_protocol_background_review_turn_boundary() -> None:
    """Background review section reflects 3.5c turn-boundary firing.

    Contract:
      - Exactly one paragraph follows the heading (the curator/pin paragraph
        was deleted as dead doctrine).
      - The new opening phrasing "after every ~5 tool calls" is present.
      - Stale phrasings ("session-end review") and removed commands
        ("/skills pin", "/skills curator restore") do not leak back in.
    """
    protocol_path = (
        Path(__file__).parent.parent / "co_cli" / "context" / "rules" / "06_skill_protocol.md"
    )
    text = protocol_path.read_text()

    # Locate the section body: from the heading to the next top-level heading
    # (## …) or end-of-file.
    marker = "## Background review"
    start = text.index(marker) + len(marker)
    after = text[start:]
    next_heading = after.find("\n## ")
    section = after if next_heading == -1 else after[:next_heading]

    paragraphs = [p.strip() for p in section.strip().split("\n\n") if p.strip()]
    assert len(paragraphs) == 1, (
        f"expected exactly 1 paragraph in '## Background review', got {len(paragraphs)}"
    )

    body = paragraphs[0]
    assert "after every ~5 tool calls" in body
    assert "session-end review" not in body
    assert "/skills pin" not in body
    assert "/skills curator restore" not in body
    assert "curator" not in body
