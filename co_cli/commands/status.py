"""Slash command handler for /status."""

from __future__ import annotations

from co_cli.commands.types import CommandContext
from co_cli.display._core import console


async def _cmd_status(ctx: CommandContext, args: str) -> None:
    """Show system health, or task status when <id> is given."""
    task_id = args.strip()
    if task_id:
        state = ctx.deps.session.background_tasks.get(task_id)
        if state is None:
            console.print(f"[bold red]Task not found:[/bold red] {task_id}")
            return None
        from rich.table import Table

        table = Table(title=f"Task: {task_id}", border_style="accent", expand=False)
        table.add_column("Field", style="accent")
        table.add_column("Value")
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

    from co_cli.bootstrap.render_status import (
        check_security,
        get_status,
        render_security_findings,
        render_status_table,
    )

    info = get_status(ctx.deps.config, tool_count=len(ctx.deps.tool_index))
    console.print(render_status_table(info))
    findings = check_security()
    render_security_findings(findings)
    return None
