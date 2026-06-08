"""Skill index lifecycle helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.deps import CoDeps
    from co_cli.skills.skill_types import SkillInfo


def set_skill_catalog(new_skills: dict[str, SkillInfo], deps: CoDeps) -> None:
    """Replace deps.skill_catalog with the new skill set."""
    deps.skill_catalog = new_skills


def get_skill_catalog(skill_catalog: dict[str, SkillInfo]) -> list[dict]:
    """Derive model-facing skill descriptors from skill_catalog."""
    return [
        {"name": s.name, "description": s.description}
        for s in skill_catalog.values()
        if s.description and not s.disable_model_invocation
    ]
