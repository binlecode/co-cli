"""Slash command handler for /tasks."""

from __future__ import annotations

from rich.table import Table

from co_cli.commands._types import CommandContext
from co_cli.display._core import console


async def _cmd_tasks(ctx: CommandContext, args: str) -> None:
    """List background tasks. Usage: /tasks [status]"""
    status_filter = args.strip() or None
    tasks_dict = ctx.deps.session.background_tasks
    if status_filter:
        tasks = [s for s in tasks_dict.values() if s.status == status_filter]
    else:
        tasks = list(tasks_dict.values())

    if not tasks:
        filter_note = f" with status={status_filter}" if status_filter else ""
        console.print(f"[dim]No background tasks{filter_note}.[/dim]")
        return None

    label = f"Background Tasks ({status_filter or 'all'})"
    table = Table(title=label, border_style="accent", expand=False)
    table.add_column("Task ID", style="accent")
    table.add_column("Status")
    table.add_column("Command")
    table.add_column("Started")
    for s in tasks:
        started = (s.started_at or "")[:19]
        table.add_row(s.task_id, s.status, s.command, started)
    console.print(table)
    return None
