"""Slash command handler for /tools."""

from __future__ import annotations

from co_cli.commands.types import CommandContext
from co_cli.display.core import console


async def _cmd_tools(ctx: CommandContext, args: str) -> None:
    """List registered agent tools."""
    tools = sorted(ctx.deps.tool_index.keys())
    lines = [f"  [accent]{i + 1}.[/accent] {name}" for i, name in enumerate(tools)]
    console.print(f"[info]Registered tools ({len(tools)}):[/info]")
    console.print("\n".join(lines))
    return None
