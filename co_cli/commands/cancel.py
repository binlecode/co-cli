"""Slash command handler for /cancel."""

from __future__ import annotations

from co_cli.commands.types import CommandContext
from co_cli.display.core import console
from co_cli.tools.background import BackgroundCleanupError, kill_task


async def _cmd_cancel(ctx: CommandContext, args: str) -> None:
    """Cancel a running background task. Usage: /cancel <task_id>"""
    task_id = args.strip()
    if not task_id:
        console.print("[bold red]Usage:[/bold red] /cancel <task_id>")
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
    console.print(f"[success]✓ Cancelled task {task_id}[/success]")
    return None
