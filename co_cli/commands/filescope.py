"""Slash command handler for /filescope."""

from __future__ import annotations

from co_cli.commands.types import CommandContext
from co_cli.display.core import console


async def _cmd_filescope(ctx: CommandContext, args: str) -> None:
    """Show the resolved file search roots (read scope) and workspace write anchor."""
    roots = ctx.deps.file_search_roots
    workspace_dir = ctx.deps.workspace_dir
    is_default = roots == [workspace_dir]
    console.print("[info]File search roots (read scope):[/info]")
    if is_default:
        console.print(
            f"  [accent]1.[/accent] {workspace_dir}        (default scope — workspace only)",
            soft_wrap=True,
        )
    else:
        for i, root in enumerate(roots):
            marker = "" if root.exists() else "        (missing)"
            console.print(f"  [accent]{i + 1}.[/accent] {root}{marker}", soft_wrap=True)
    console.print(f"[info]Write anchor (workspace_dir):[/info] {workspace_dir}", soft_wrap=True)
    return None
