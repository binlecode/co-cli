"""Slash command handler for /help."""

from __future__ import annotations

from rich.table import Table

from co_cli.commands.registry import BUILTIN_COMMANDS
from co_cli.commands.types import CommandContext
from co_cli.display.core import console


async def _cmd_help(ctx: CommandContext, args: str) -> None:
    """List available slash commands."""
    table = Table(title="Slash Commands", border_style="accent", expand=False)
    table.add_column("Command", style="accent")
    table.add_column("Description")
    for cmd in BUILTIN_COMMANDS.values():
        table.add_row(f"/{cmd.name}", cmd.description)
    if ctx.deps.skill_commands:
        for skill in ctx.deps.skill_commands.values():
            if skill.user_invocable:
                hint = f"  [{skill.argument_hint}]" if skill.argument_hint else ""
                table.add_row(f"/{skill.name}{hint}", skill.description or "(skill)")
    console.print(table)
    console.print(
        "[dim]Usage: /status shows system health; /status <task-id> shows a background task.[/dim]"
    )
    return None
