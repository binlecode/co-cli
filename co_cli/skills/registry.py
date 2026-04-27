"""Skill registry lifecycle helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.deps import CoDeps
    from co_cli.skills._skill_types import SkillConfig


def set_skill_commands(new_skills: dict[str, SkillConfig], deps: CoDeps) -> None:
    """Replace deps.skill_commands with the new skill set."""
    deps.skill_commands = new_skills
