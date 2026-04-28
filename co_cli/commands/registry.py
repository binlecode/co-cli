"""Leaf slash-command registry — types, dict, completer helpers, namespace filter.

This module imports only from stdlib, co_cli.commands.types, and
co_cli.skills.skill_types. It must never import from sibling handler modules.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from co_cli.commands.types import CommandContext, ReplaceTranscript
from co_cli.skills.skill_types import SkillConfig


@dataclass(frozen=True)
class SlashCommand:
    """A registered slash command."""

    name: str
    description: str
    handler: Callable[[CommandContext, str], Awaitable[list[Any] | ReplaceTranscript | None]]


BUILTIN_COMMANDS: dict[str, SlashCommand] = {}


def build_completer_words(skill_commands: dict) -> list[str]:
    """Single source of truth for the REPL tab-completer word list."""
    return [f"/{name}" for name in BUILTIN_COMMANDS] + [
        f"/{name}" for name, s in skill_commands.items() if s.user_invocable
    ]


def _refresh_completer(ctx: CommandContext) -> None:
    """Refresh the REPL completer words after a skill_commands mutation."""
    if ctx.completer is None:
        return
    ctx.completer.words = build_completer_words(ctx.deps.skill_commands)


def filter_namespace_conflicts(
    loaded: dict[str, SkillConfig],
    reserved: set[str],
    errors: list[str] | None = None,
) -> dict[str, SkillConfig]:
    """Drop skills whose name shadows a reserved slash-command name.

    Records dropped names to errors. Pure data-in/data-out.
    """
    accepted: dict[str, SkillConfig] = {}
    for name, skill in loaded.items():
        if name in reserved:
            if errors is not None:
                errors.append(f"skill '{name}' skipped: shadows built-in slash command")
            continue
        accepted[name] = skill
    return accepted
