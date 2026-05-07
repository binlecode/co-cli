"""Slash command handler for /tasks."""

from __future__ import annotations

import re

from co_cli.commands.types import CommandContext
from co_cli.display.core import console, make_table

_TASK_ID_RE = re.compile(r"^[0-9a-f]{8,}$")


async def _cmd_tasks(ctx: CommandContext, args: str) -> None:
    """List background tasks. Usage: /tasks [status-filter | task-id]"""
    arg = args.strip()
    tasks_dict = ctx.deps.session.background_tasks

    if arg and _TASK_ID_RE.fullmatch(arg):
        state = tasks_dict.get(arg)
        if state is None:
            console.print(f"[bold red]Task not found:[/bold red] {arg}")
            return None
        table = make_table("Field", "Value")
        for k, v in [
            ("task_id", state.task_id),
            ("status", state.status),
            ("command", state.command),
            ("description", state.description),
            ("started_at", state.started_at),
            ("completed_at", state.completed_at or ""),
            ("exit_code", str(state.exit_code) if state.exit_code is not None else ""),
        ]:
            table.add_row(k, v)
        console.print(table)
        lines = list(state.output_lines)[-20:]
        if lines:
            console.print("[dim]--- Output (last 20 lines) ---[/dim]")
            for line in lines:
                console.print(line)
        return None

    status_filter = arg or None
    if status_filter:
        tasks = [s for s in tasks_dict.values() if s.status == status_filter]
    else:
        tasks = list(tasks_dict.values())

    if not tasks:
        filter_note = f" with status={status_filter}" if status_filter else ""
        console.print(f"[dim]No background tasks{filter_note}.[/dim]")
        return None

    table = make_table("Task ID", "Status", "Command", "Started")
    for s in tasks:
        started = (s.started_at or "")[:19]
        table.add_row(s.task_id, s.status, s.command, started)
    console.print(table)
    return None
