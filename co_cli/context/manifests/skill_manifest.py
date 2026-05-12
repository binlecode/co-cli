"""Bundled skill manifest — declares bundled skills in the static system prompt.

Bundled skills land via the manifest (always-visible, cache-stable, ~300 tokens).
User-installed skills land via skill_search (query-driven, on-demand). The split
keeps the prompt small while the long tail stays discoverable.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.skills.skill_types import SkillConfig


def render_skill_manifest(
    skill_commands: dict[str, SkillConfig],
    skills_dir: Path,
    user_skills_dir: Path,
) -> str:
    """Render `<available_skills>` for bundled skills only — empty string if none.

    Bundled = present in skills_dir and not shadowed by a same-named file in
    user_skills_dir. Filter at render time (no frontmatter metadata required).
    """
    bundled_names = sorted(
        name
        for name in skill_commands
        if (skills_dir / f"{name}.md").is_file() and not (user_skills_dir / f"{name}.md").is_file()
    )
    if not bundled_names:
        return ""

    lines: list[str] = ["<available_skills>"]
    for name in bundled_names:
        skill = skill_commands[name]
        description = (skill.description or "").strip()
        lines.append(
            f'  <skill name="{escape(name, quote=True)}" '
            f'description="{escape(description, quote=True)}" />'
        )
    lines.append("</available_skills>")
    return "\n".join(lines)
