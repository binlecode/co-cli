"""Slash command handler for /queue (list/clear/pop)."""

from __future__ import annotations

from co_cli.commands._queue_control import run_queue_control
from co_cli.commands.types import CommandContext


async def _cmd_queue(ctx: CommandContext, args: str) -> None:
    """Inspect or prune pending REPL input-queue items.

    Usage: /queue [list|clear|pop [n]]
      /queue        → list pending items (1-based indices, truncated preview)
      /queue clear  → drop all pending items
      /queue pop    → drop the last item
      /queue pop n  → drop item at 1-based index n
    """
    run_queue_control(ctx.input_queue, args)
    return None
