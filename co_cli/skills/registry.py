"""Skill registry lifecycle helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.deps import CoDeps
    from co_cli.skills.skill_types import SkillConfig


def set_skill_commands(new_skills: dict[str, SkillConfig], deps: CoDeps) -> None:
    """Replace deps.skill_commands with the new skill set."""
    deps.skill_commands = new_skills


def get_skill_registry(skill_commands: dict[str, SkillConfig]) -> list[dict]:
    """Derive model-facing skill registry from skill_commands."""
    return [
        {"name": s.name, "description": s.description}
        for s in skill_commands.values()
        if s.description and not s.disable_model_invocation
    ]
