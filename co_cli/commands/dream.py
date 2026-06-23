"""CLI command group for ``co dream`` — daemon lifecycle and status."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import typer

from co_cli.config.core import DREAM_TIDY_TAG, USER_DIR
from co_cli.fileio.atomic import atomic_write_text

if TYPE_CHECKING:
    from co_cli.commands.types import CommandContext

dream_app = typer.Typer(
    name="dream",
    help="Manage the dream daemon.",
    invoke_without_command=True,
)


async def handle_dream_slash(ctx: CommandContext, args: str) -> None:
    """Handle the /dream slash command — status (default) plus start | stop | tidy.

    Routes to the existing detached ``process.py`` control surface; the daemon's
    lifetime stays independent of the REPL. ``start`` works regardless of
    ``dream.autostart`` (which gates only REPL auto-spawn on launch).
    """
    from co_cli.daemons.dream.process import status_daemon
    from co_cli.display.core import console

    tokens = args.strip().split()
    sub = tokens[0].lower() if tokens else ""

    if sub in ("", "status"):
        _print_dream_status(ctx, status_daemon(USER_DIR))
        return

    if sub == "start":
        if status_daemon(USER_DIR).get("running"):
            console.print("[info]Dream daemon:[/info]  [accent]already running[/accent]")
            return
        from co_cli.daemons.dream.process import start_daemon

        try:
            start_daemon(USER_DIR, origin="slash")
        except SystemExit:
            pass
        return

    if sub == "stop":
        if "force" not in (token.lower() for token in tokens[1:]):
            console.print(
                "[yellow]Dream daemon is shared across every co session attached to this "
                "CO_HOME.[/yellow]"
            )
            console.print(
                "  Stopping pauses curation for all of them — queued reviews resume on the "
                "next start."
            )
            console.print("  [dim]hint:[/dim] `/dream stop force` to stop anyway")
            return
        from co_cli.daemons.dream.process import stop_daemon

        stop_daemon(USER_DIR)
        return

    if sub == "tidy":
        if not status_daemon(USER_DIR).get("running"):
            console.print("[info]Dream daemon:[/info]  [yellow]not running[/yellow]")
            console.print("  [dim]hint:[/dim] `/dream start` to start it first")
            return
        atomic_write_text(DREAM_TIDY_TAG, "")
        console.print("Housekeeping requested. Check `/dream` for results.")
        return

    console.print("[dim]usage:[/dim] /dream [status | start | stop | tidy]")


def _print_dream_status(ctx: CommandContext, status: dict) -> None:
    """Render the read-only daemon status block."""
    from co_cli.display.core import console

    if status.get("running"):
        console.print("[info]Dream daemon:[/info]  [accent]running[/accent]")
        for key, value in status.items():
            console.print(f"  [dim]{key}:[/dim] {value}")
        return

    queue_depth = status.get("queue_depth", 0)
    console.print("[info]Dream daemon:[/info]  [yellow]not running[/yellow]")
    console.print(f"  [dim]queue (on disk):[/dim] {queue_depth}")
    if ctx.deps.config.dream.autostart:
        console.print("  [dim]hint:[/dim] `/dream start` to start manually")
    else:
        console.print(
            "  [dim]hint:[/dim] `/dream start` to start now "
            "(or set dream.autostart=true to spawn on REPL launch)"
        )


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
    yes: bool = typer.Option(
        False, "--yes", help="Confirm the stop (graceful SIGTERM) without the shared-daemon prompt"
    ),
    force: bool = typer.Option(
        False, "--force", help="SIGKILL immediately, skipping the SIGTERM grace period"
    ),
) -> None:
    """Stop the dream daemon.

    The daemon is shared across every co session attached to this CO_HOME, so a
    stop pauses curation for all of them. Requires explicit confirmation: pass
    ``--yes`` for a graceful stop or ``--force`` for an immediate SIGKILL (which
    also implies confirmation).
    """
    from co_cli.daemons.dream.process import stop_daemon

    if not yes and not force:
        typer.echo(
            "dream daemon is shared across every co session attached to this CO_HOME; "
            "stopping pauses curation for all of them (queued reviews resume on next start). "
            "Use --yes (graceful) or --force (SIGKILL) to confirm.",
            err=True,
        )
        return

    stop_daemon(USER_DIR, force=force)


@dream_app.command("tidy")
def dream_tidy() -> None:
    """Request a one-shot housekeeping pass from the running daemon.

    Writes a sentinel file the daemon picks up on its next idle tick;
    worst-case latency is ``dream.tick_interval_seconds``. Errors if the daemon
    is not running — does NOT spawn an ad-hoc pass.
    """
    from co_cli.daemons.dream.process import status_daemon

    status = status_daemon(USER_DIR)
    if not status.get("running"):
        typer.echo("dream daemon not running; start with `co dream start`.", err=True)
        raise typer.Exit(code=1)

    atomic_write_text(DREAM_TIDY_TAG, "")
    typer.echo("Housekeeping requested. Check `co dream status` for results.")
