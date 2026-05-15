"""Skill types for the skills domain.

``SkillConfig`` is a frozen dataclass representing a loaded-skill record (one instantiated skill
command). It is NOT a settings model. For config/settings see ``SkillsSettings`` in
``co_cli.config.skills``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SkillConfig:
    """A dynamically-loaded skill command (from bundled co_cli/skills/ or user ~/.co-cli/skills/).

    # source_url is not a field — it is read from frontmatter in command handlers
    """

    name: str
    description: str = ""
    body: str = ""
    argument_hint: str = ""
    user_invocable: bool = True
    disable_model_invocation: bool = False
    requires: dict = field(default_factory=dict)
    skill_env: dict[str, str] = field(default_factory=dict)
    path: Path | None = None
