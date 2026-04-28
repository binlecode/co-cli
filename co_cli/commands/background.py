"""Slash command handler for /background."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from co_cli.commands.types import CommandContext
from co_cli.display.core import console
from co_cli.tools.background import BackgroundTaskState, make_task_id, spawn_task


async def _cmd_background(ctx: CommandContext, args: str) -> None:
    """Run a command in the background. Usage: /background <cmd>"""
    cmd = args.strip()
    if not cmd:
        console.print("[bold red]Usage:[/bold red] /background <command>")
        console.print("[dim]Example: /background uv run pytest[/dim]")
        return None

    task_id = make_task_id()
    state = BackgroundTaskState(
        task_id=task_id,
        command=cmd,
        cwd=str(Path.cwd()),
        description=cmd,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
    )
    ctx.deps.session.background_tasks[task_id] = state
    try:
        await spawn_task(state, ctx.deps.session)
        console.print(f"[success][{task_id}] started[/success]")
        console.print(f"[dim]Use /status {task_id} to check progress.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to start background task:[/bold red] {e}")
    return None
