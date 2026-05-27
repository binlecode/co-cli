"""Package-private utilities shared across command handlers."""

from __future__ import annotations

from co_cli.commands.types import CommandContext


async def _confirm(ctx: CommandContext, msg: str) -> bool:
    """Prompt user with msg; return True iff they confirmed (frontend or fallback)."""
    from co_cli.display.core import console

    if ctx.frontend:
        return await ctx.frontend.prompt_confirm(msg)
    # No-frontend fallback (no owned app in this path) — a direct sync read, not
    # wrapped in run_in_terminal (CD-m-6).
    return console.input(msg).strip().lower() == "y"
