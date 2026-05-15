"""Skill manifest — declares all discoverable skills in the static system prompt."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.skills.skill_types import SkillInfo


def render_skill_manifest(
    skill_index: dict[str, SkillInfo],
    skills_dir: Path,
    user_skills_dir: Path,
) -> str:
    """Render `<available_skills>` for all discoverable skills — empty string if none.

    All entries in skill_index are emitted: bundled and user-installed.
    For a name present in both directories the user-dir description wins
    (skill_index[name] already carries the shadowed value from the loader).
    """
    all_names = sorted(skill_index)
    if not all_names:
        return ""

    lines: list[str] = ["<available_skills>"]
    for name in all_names:
        skill = skill_index[name]
        description = (skill.description or "").strip()
        lines.append(
            f'  <skill name="{escape(name, quote=True)}" '
            f'description="{escape(description, quote=True)}" />'
        )
    lines.append("</available_skills>")
    return "\n".join(lines)
