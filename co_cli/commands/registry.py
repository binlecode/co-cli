"""Leaf slash-command registry — types, dict, completer helpers, namespace filter.

This module imports only from stdlib, co_cli.commands.types, and
co_cli.skills.skill_types. It must never import from sibling handler modules.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from co_cli.commands.types import CommandContext, ReplaceTranscript
from co_cli.skills.skill_types import SkillInfo


@dataclass(frozen=True)
class SlashCommand:
    """A registered slash command."""

    name: str
    description: str
    handler: Callable[[CommandContext, str], Awaitable[list[Any] | ReplaceTranscript | None]]


BUILTIN_COMMANDS: dict[str, SlashCommand] = {}


def build_completer_entries(skill_catalog: dict) -> list[tuple[str, str]]:
    """Single source of truth for the REPL completer: (name, description) pairs."""
    builtin = [(name, cmd.description) for name, cmd in BUILTIN_COMMANDS.items()]
    skills = [(name, s.description) for name, s in skill_catalog.items() if s.user_invocable]
    return builtin + skills


def refresh_completer(ctx: CommandContext) -> None:
    """Refresh the REPL completer after a skill_catalog mutation."""
    if ctx.completer is None:
        return
    ctx.completer.update(build_completer_entries(ctx.deps.skill_catalog))


def filter_namespace_conflicts(
    loaded: dict[str, SkillInfo],
    reserved: set[str],
    errors: list[str] | None = None,
) -> dict[str, SkillInfo]:
    """Drop skills whose name shadows a reserved slash-command name.

    Records dropped names to errors. Pure data-in/data-out.
    """
    accepted: dict[str, SkillInfo] = {}
    for name, skill in loaded.items():
        if name in reserved:
            if errors is not None:
                errors.append(f"skill '{name}' skipped: shadows built-in slash command")
            continue
        accepted[name] = skill
    return accepted
