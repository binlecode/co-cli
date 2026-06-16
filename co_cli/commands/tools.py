"""Slash command handler for /tools."""

from __future__ import annotations

from co_cli.commands.types import CommandContext
from co_cli.deps import VisibilityPolicyEnum
from co_cli.display.core import console, make_table


async def _cmd_tools(ctx: CommandContext, args: str) -> None:
    """List registered agent tools."""
    catalog = ctx.deps.tool_catalog
    names = sorted(catalog.keys())
    console.print(f"[info]Registered tools ({len(names)}):[/info]")
    console.print("[dim]● always-loaded   ○ deferred[/dim]")
    table = make_table("Visibility", "Index", "Name", "Description")
    for i, name in enumerate(names):
        info = catalog[name]
        is_deferred = info.visibility == VisibilityPolicyEnum.DEFERRED
        indicator = "[dim]○[/dim]" if is_deferred else "[success]●[/success]"
        table.add_row(
            indicator,
            f"[accent]{i + 1}.[/accent]",
            name,
            info.description or "",
        )
    console.print(table)
    return None
