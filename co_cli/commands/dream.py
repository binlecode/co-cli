"""CLI command group for ``co dream`` — daemon lifecycle and status."""

from __future__ import annotations

import json
import socket
from typing import TYPE_CHECKING

import typer

from co_cli.config.core import (
    DREAM_QUEUE_DIR,
    DREAM_QUEUE_FAILED_DIR,
    DREAM_SOCK,
    USER_DIR,
)

if TYPE_CHECKING:
    from co_cli.commands.types import CommandContext

dream_app = typer.Typer(
    name="dream",
    help="Manage the dream daemon.",
    invoke_without_command=True,
)


def _socket_status(timeout_ms: int = 2000) -> dict | None:
    """Connect to the dream daemon socket and return the parsed STATUS response.

    Returns ``None`` on any error (socket unreachable, timeout, bad JSON).
    Never raises into the caller.
    """
    try:
        timeout_s = timeout_ms / 1000.0
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            sock.connect(str(DREAM_SOCK))
            sock.sendall(b"STATUS\n")
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
        line = data.split(b"\n", 1)[0].decode()
        return json.loads(line)
    except Exception:
        return None


async def handle_dream_slash(ctx: CommandContext, args: str) -> None:
    """Handle the /dream slash command — read-only daemon inspection."""
    from co_cli.display.core import console

    status = _socket_status(timeout_ms=500)

    if isinstance(status, dict):
        console.print("[info]Dream daemon:[/info]  [accent]running[/accent]")
        for key, value in status.items():
            console.print(f"  [dim]{key}:[/dim] {value}")
        return

    deps = ctx.deps
    if deps.config.dream.enabled:
        queue_depth = (
            len([f for f in DREAM_QUEUE_DIR.glob("*.json") if not f.name.endswith(".tmp")])
            if DREAM_QUEUE_DIR.exists()
            else 0
        )
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
        False, "--foreground/--no-foreground", help="Run in the foreground (after double-fork)"
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
    running = raw.get("running", False)

    if not running:
        queue_depth = len(list(DREAM_QUEUE_DIR.glob("*.json"))) if DREAM_QUEUE_DIR.exists() else 0
        failed_count = (
            len(list(DREAM_QUEUE_FAILED_DIR.glob("*.json")))
            if DREAM_QUEUE_FAILED_DIR.exists()
            else 0
        )
        typer.echo(
            json.dumps(
                {"running": False, "queue_depth": queue_depth, "failed_count": failed_count},
                indent=2,
            )
        )
        return

    # Daemon is running — enrich with socket status
    socket_data = _socket_status()
    if socket_data is None:
        socket_data = raw

    queue_depth = socket_data.get("queue_depth", 0)
    failed_count = socket_data.get("failed_count", 0)

    # Count attempts_pending: queue files that have attempts > 0
    attempts_pending = 0
    if DREAM_QUEUE_DIR.exists():
        for queue_file in DREAM_QUEUE_DIR.glob("*.json"):
            try:
                payload = json.loads(queue_file.read_text())
                if payload.get("attempts", 0) > 0:
                    attempts_pending += 1
            except (json.JSONDecodeError, OSError):
                pass

    output = {
        "running": True,
        "pid": socket_data.get("pid"),
        "uptime_seconds": socket_data.get("uptime_seconds"),
        "queue_depth": queue_depth,
        "current_item": socket_data.get("current_item"),
        "attempts_pending": attempts_pending,
        "failed_count": failed_count,
        "spawn_origin": socket_data.get("spawn_origin"),
        "spawn_session_id": socket_data.get("spawn_session_id"),
    }
    typer.echo(json.dumps(output, indent=2))


@dream_app.command("stop")
def dream_stop(
    force: bool = typer.Option(False, "--force/--no-force", help="Force-kill via SIGTERM"),
) -> None:
    """Stop the dream daemon."""
    from co_cli.daemons.dream.process import stop_daemon

    stop_daemon(USER_DIR, force=force)
