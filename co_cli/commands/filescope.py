"""Slash command handler for /filescope."""

from __future__ import annotations

from co_cli.commands.types import CommandContext
from co_cli.display.core import console, glyphs


async def _cmd_filescope(ctx: CommandContext, args: str) -> None:
    """Show the resolved file search roots (read scope) and workspace write anchor."""
    roots = ctx.deps.file_search_roots
    workspace_dir = ctx.deps.workspace_dir
    is_default = roots == [workspace_dir]
    console.print("[info]File search roots (read scope):[/info]")
    console.print(f"[dim]{glyphs().success} present   {glyphs().error} missing[/dim]")
    for i, root in enumerate(roots):
        indicator = (
            f"[success]{glyphs().success}[/success]"
            if root.exists()
            else f"[dim]{glyphs().error}[/dim]"
        )
        if is_default:
            note = "  (default scope — workspace only)"
        elif not root.exists():
            note = "  (missing)"
        else:
            note = ""
        console.print(
            f"{indicator} [accent]{i + 1}.[/accent] {root}{note}",
            soft_wrap=True,
        )
    console.print(f"[info]Write anchor (workspace_dir):[/info] {workspace_dir}", soft_wrap=True)
    return None
