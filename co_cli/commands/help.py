"""Slash command handler for /help."""

from __future__ import annotations

from rich.markup import escape

from co_cli.commands.registry import BUILTIN_COMMANDS
from co_cli.commands.types import CommandContext
from co_cli.display.core import console, make_table


async def _cmd_help(ctx: CommandContext, args: str) -> None:
    """List available slash commands."""
    table = make_table("Command", "Description")
    for cmd in BUILTIN_COMMANDS.values():
        table.add_row(f"/{cmd.name}", escape(cmd.description))
    if ctx.deps.skill_catalog:
        for skill in ctx.deps.skill_catalog.values():
            if skill.user_invocable:
                hint = f"  [{skill.argument_hint}]" if skill.argument_hint else ""
                table.add_row(
                    escape(f"/{skill.name}{hint}"), escape(skill.description or "(skill)")
                )
    console.print(table)
    return None
