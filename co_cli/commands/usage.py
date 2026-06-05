"""Slash command handler for /usage — token-usage windows from the ledger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from co_cli.commands.types import CommandContext
from co_cli.display.core import console, make_table
from co_cli.session.usage import ORIGIN_SESSION, UsageTotals, aggregate

_WINDOWS: dict[str, tuple[str, timedelta | None]] = {
    "week": ("Last 7 days", timedelta(days=7)),
    "month": ("Last 30 days", timedelta(days=30)),
    "total": ("All time", None),
}


def _totals_row(table, label: str, totals: UsageTotals) -> None:
    """Append one input/output/total row to the table."""
    table.add_row(
        label,
        f"{totals.input_tokens:,}",
        f"{totals.output_tokens:,}",
        f"{totals.total:,}",
    )


async def _cmd_usage(ctx: CommandContext, args: str) -> None:
    """Show token usage: /usage [week|month|total].

    No arg shows the current session only. The windowed views (week/month/total)
    split usage into Session / Daemon / Total subtotals.
    """
    arg = args.strip().lower()

    if not arg:
        session_id = ctx.deps.session.session_path.stem[-8:]
        window = aggregate(ctx.deps.usage_log_path, session_id=session_id, origin=ORIGIN_SESSION)
        table = make_table("Scope", "Input", "Output", "Total")
        _totals_row(table, "Current session", window.session)
        console.print(table)
        return None

    if arg not in _WINDOWS:
        valid = ", ".join(_WINDOWS)
        console.print(f"[bold red]Unknown /usage argument:[/bold red] {arg}")
        console.print(f"[dim]Valid arguments: {valid}[/dim]")
        return None

    label, delta = _WINDOWS[arg]
    since = None if delta is None else datetime.now(UTC) - delta
    window = aggregate(ctx.deps.usage_log_path, since=since)

    console.print(
        f"[bold]Token usage — {label}[/bold] [dim]({window.session_count} session(s))[/dim]"
    )
    table = make_table("Scope", "Input", "Output", "Total")
    _totals_row(table, "Session", window.session)
    _totals_row(table, "Daemon", window.daemon)
    _totals_row(table, "Total", window.total)
    console.print(table)
    return None
