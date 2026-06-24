"""Slash command handler for /tasks — the background-task subsystem entry point.

`/tasks` is the single command for background tasks (idiom-aligned with `/dream`,
`/memory`, `/queue`): bare lists status, `run` launches, `cancel` terminates, a
task-id shows detail, anything else filters the list by status.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from co_cli.commands.types import CommandContext
from co_cli.display.core import console, glyphs, make_table
from co_cli.tools.background import (
    BackgroundCleanupError,
    BackgroundTaskState,
    kill_task,
    make_task_id,
    spawn_task,
    tail_log,
)

_TASK_ID_RE = re.compile(r"^[0-9a-f]{8,}$")


async def _cmd_tasks(ctx: CommandContext, args: str) -> None:
    """Manage background tasks. Usage: /tasks [run <cmd> | cancel <id> | <status> | <task-id>]"""
    arg = args.strip()
    verb, _, rest = arg.partition(" ")
    if verb.lower() == "run":
        return await _run_task(ctx, rest.strip())
    if verb.lower() == "cancel":
        return await _cancel_task(ctx, rest.strip())
    return _show_tasks(ctx, arg)


async def _run_task(ctx: CommandContext, cmd: str) -> None:
    """Launch a command in the background (was /background)."""
    if not cmd:
        console.print("[bold red]Usage:[/bold red] /tasks run <command>")
        console.print("[dim]Example: /tasks run uv run pytest[/dim]")
        return None

    task_id = make_task_id()
    state = BackgroundTaskState(
        task_id=task_id,
        command=cmd,
        cwd=str(ctx.deps.workspace_dir),
        description=cmd,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
    )
    ctx.deps.session.background_tasks[task_id] = state
    try:
        await spawn_task(state, ctx.deps.session)
        console.print(f"[success][{task_id}] started[/success]")
        console.print(f"[dim]Use /tasks {task_id} to check progress.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to start background task:[/bold red] {e}")
    return None


async def _cancel_task(ctx: CommandContext, task_id: str) -> None:
    """Terminate a running task (SIGTERM→SIGKILL; was /cancel)."""
    if not task_id:
        console.print("[bold red]Usage:[/bold red] /tasks cancel <task_id>")
        return None

    state = ctx.deps.session.background_tasks.get(task_id)
    if state is None:
        console.print(f"[bold red]Task not found:[/bold red] {task_id}")
        return None

    if state.status != "running":
        console.print(f"[dim]Task {task_id} is not running (status={state.status}).[/dim]")
        return None

    try:
        await kill_task(state)
    except BackgroundCleanupError as e:
        console.print(f"[bold red]Cancel cleanup failed:[/bold red] {e}")
        return None
    console.print(f"[success]{glyphs().success} Cancelled task {task_id}[/success]")
    return None


def _show_tasks(ctx: CommandContext, arg: str) -> None:
    """List tasks (optionally status-filtered) or show one task's detail by id."""
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
        lines = [state.spawn_error] if state.spawn_error else tail_log(state.log_path, 20)
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
