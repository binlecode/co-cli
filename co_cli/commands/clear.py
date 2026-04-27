"""Slash command handler for /clear."""

from __future__ import annotations

from typing import Any

from co_cli.commands._types import CommandContext
from co_cli.display._core import console


async def _cmd_clear(ctx: CommandContext, args: str) -> list[Any]:
    """Clear conversation history."""
    ctx.deps.runtime.previous_compaction_summary = None
    console.print("[info]Conversation history cleared.[/info]")
    return []
