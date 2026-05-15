"""Skill registry lifecycle helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.deps import CoDeps
    from co_cli.skills.skill_types import SkillConfig


def set_skill_registry(new_skills: dict[str, SkillConfig], deps: CoDeps) -> None:
    """Replace deps.skill_registry with the new skill set."""
    deps.skill_registry = new_skills


def get_skill_registry(skill_registry: dict[str, SkillConfig]) -> list[dict]:
    """Derive model-facing skill registry from skill_registry."""
    return [
        {"name": s.name, "description": s.description}
        for s in skill_registry.values()
        if s.description and not s.disable_model_invocation
    ]
