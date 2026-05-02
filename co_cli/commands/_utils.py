"""Package-private utilities shared across command handlers."""

from __future__ import annotations

from co_cli.commands.types import CommandContext


def _confirm(ctx: CommandContext, msg: str) -> bool:
    """Prompt user with msg; return True iff they confirmed (frontend or fallback)."""
    from co_cli.display.core import console

    if ctx.frontend:
        return ctx.frontend.prompt_confirm(msg)
    return console.input(msg).strip().lower() == "y"
