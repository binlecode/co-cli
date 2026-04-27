"""Slash command handler for /sessions."""

from __future__ import annotations

from rich.table import Table

from co_cli.commands._types import CommandContext
from co_cli.display._core import console


async def _cmd_sessions(ctx: CommandContext, args: str) -> None:
    """List past sessions, optionally filtered by keyword."""
    from co_cli.memory.session_browser import format_file_size, list_sessions

    summaries = list_sessions(ctx.deps.sessions_dir)
    if args:
        keyword = args.lower()
        summaries = [s for s in summaries if keyword in s.title.lower()]

    if not summaries:
        console.print("[dim]No sessions found.[/dim]")
        return None

    table = Table(title="Sessions", border_style="accent", expand=False)
    table.add_column("Title", style="accent")
    table.add_column("Date")
    table.add_column("Size")
    for s in summaries:
        table.add_row(
            s.title,
            s.last_modified.strftime("%Y-%m-%d %H:%M"),
            format_file_size(s.file_size),
        )
    console.print(table)
    return None
