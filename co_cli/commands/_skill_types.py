"""Skill configuration type for the commands package.

Extracted from _commands.py to break the circular import between deps.py and
commands/_commands.py. This dataclass has no dependency on deps.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SkillConfig:
    """A dynamically-loaded skill command (from .co-cli/skills/*.md).

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
