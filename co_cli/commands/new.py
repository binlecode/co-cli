"""Slash command handler for /new."""

from __future__ import annotations

from typing import Any

from co_cli.commands._types import CommandContext
from co_cli.display._core import console


async def _cmd_new(ctx: CommandContext, _args: str) -> list[Any] | None:
    """Start a fresh session."""
    from co_cli.memory.session import new_session_path

    if not ctx.message_history:
        console.print("[dim]Nothing to rotate — history is empty.[/dim]")
        return None

    ctx.deps.session.session_path = new_session_path(ctx.deps.sessions_dir)
    ctx.deps.runtime.previous_compaction_summary = None
    ctx.deps.runtime.post_compaction_token_estimate = None
    ctx.deps.runtime.message_count_at_last_compaction = None
    console.print("[dim]Session rotated.[/dim]")
    return []
