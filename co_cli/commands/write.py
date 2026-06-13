"""Slash command handler for /write."""

from __future__ import annotations

from co_cli.commands.types import CommandContext
from co_cli.display.core import console
from co_cli.tools.background import TaskInputError, write_to_task


async def _cmd_write(ctx: CommandContext, args: str) -> None:
    """Write a line to a running background task's stdin. Usage: /write <id> <input>

    The first whitespace-delimited token is the task id; everything after it is
    passed to stdin verbatim (spaces, quotes, and punctuation preserved) with a
    trailing newline. The input is never shlex-split or otherwise tokenized.
    """
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        console.print("[bold red]Usage:[/bold red] /write <task_id> <input>")
        console.print("[dim]Example: /write a1b2c3 y[/dim]")
        return None
    task_id, text = parts[0], parts[1]

    state = ctx.deps.session.background_tasks.get(task_id)
    if state is None:
        console.print(f"[bold red]Task not found:[/bold red] {task_id}")
        return None
    try:
        await write_to_task(state, text, newline=True)
    except TaskInputError as e:
        console.print(f"[bold red]Write failed:[/bold red] {e}")
        return None
    console.print(f"[success]✓ Wrote to task {task_id}[/success]")
    console.print(f"[dim]Use /tasks {task_id} to read the response.[/dim]")
    return None
