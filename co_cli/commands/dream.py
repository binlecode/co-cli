"""CLI command group for ``co dream`` — daemon lifecycle and status."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import typer

from co_cli.config.core import DREAM_RUN_TAG, USER_DIR
from co_cli.fileio.atomic import atomic_write_text

if TYPE_CHECKING:
    from co_cli.commands.types import CommandContext

dream_app = typer.Typer(
    name="dream",
    help="Manage the dream daemon.",
    invoke_without_command=True,
)


async def handle_dream_slash(ctx: CommandContext, args: str) -> None:
    """Handle the /dream slash command — read-only daemon inspection."""
    from co_cli.daemons.dream.process import status_daemon
    from co_cli.display.core import console

    status = status_daemon(USER_DIR)

    if status.get("running"):
        console.print("[info]Dream daemon:[/info]  [accent]running[/accent]")
        for key, value in status.items():
            console.print(f"  [dim]{key}:[/dim] {value}")
        return

    deps = ctx.deps
    if deps.config.dream.enabled:
        queue_depth = status.get("queue_depth", 0)
        console.print("[info]Dream daemon:[/info]  [yellow]not running[/yellow]")
        console.print(f"  [dim]queue (on disk):[/dim] {queue_depth}")
        console.print("  [dim]hint:[/dim] 'co dream start' to start manually")
    else:
        console.print("[info]Dream daemon:[/info]  [dim]disabled[/dim]")
        console.print("  [dim]hint:[/dim] set dream.enabled=true and restart co chat")


@dream_app.callback(invoke_without_command=True)
def _dream_callback(ctx: typer.Context) -> None:
    """Manage the dream daemon."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@dream_app.command("start")
def dream_start(
    foreground: bool = typer.Option(
        False,
        "--foreground/--no-foreground",
        help="Run in the foreground (skip detached spawn via setsid)",
    ),
    origin: str = typer.Option("manual", "--origin", help="Spawn origin label"),
    session_id: str = typer.Option(
        "", "--session-id", help="Session ID to associate with this spawn"
    ),
) -> None:
    """Start the dream daemon."""
    from co_cli.daemons.dream.process import start_daemon

    start_daemon(USER_DIR, foreground=foreground, origin=origin, session_id=session_id)


@dream_app.command("status")
def dream_status() -> None:
    """Show dream daemon status."""
    from co_cli.daemons.dream.process import status_daemon

    raw = status_daemon(USER_DIR)
    typer.echo(json.dumps(raw, indent=2))


@dream_app.command("stop")
def dream_stop(
    force: bool = typer.Option(
        False, "--force", help="SIGKILL immediately, skipping the SIGTERM grace period"
    ),
) -> None:
    """Stop the dream daemon."""
    from co_cli.daemons.dream.process import stop_daemon

    stop_daemon(USER_DIR, force=force)


@dream_app.command("run")
def dream_run() -> None:
    """Request a one-shot housekeeping pass from the running daemon.

    Writes a sentinel file the daemon picks up on its next polling iteration;
    worst-case latency is ``dream.poll_interval_seconds``. Errors if the daemon
    is not running — does NOT spawn an ad-hoc pass.
    """
    from co_cli.daemons.dream.process import status_daemon

    status = status_daemon(USER_DIR)
    if not status.get("running"):
        typer.echo("dream daemon not running; start with `co dream start`.", err=True)
        raise typer.Exit(code=1)

    atomic_write_text(DREAM_RUN_TAG, "")
    typer.echo("Housekeeping requested. Check `co dream status` for results.")
