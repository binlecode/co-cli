"""Themed terminal display — console, semantic styles, display helpers."""

from rich.console import Console
from rich.panel import Panel

from co_cli.config import settings

# -- Theme colors (keyed by theme name) ------------------------------------

_COLORS: dict[str, dict[str, str]] = {
    "dark":  {"status": "yellow",      "info": "cyan", "accent": "bold cyan",  "yolo": "bold orange3"},
    "light": {"status": "dark_orange", "info": "blue", "accent": "bold blue",  "yolo": "bold orange3"},
}

# -- Console (single instance, no swapping) --------------------------------

console = Console()

# -- Indicators ------------------------------------------------------------

PROMPT_CHAR = "❯"
BULLET      = "▸"
SUCCESS     = "✦"
ERROR       = "✖"
INFO        = "◈"

# -- Display helpers -------------------------------------------------------


def _c(role: str) -> str:
    """Resolve a semantic color for the active theme."""
    return _COLORS.get(settings.theme, _COLORS["light"]).get(role, "")


def display_status(message: str, style: str | None = None) -> None:
    """Themed bullet + message."""
    s = style or _c("status")
    console.print(f"[{s}]{BULLET} {message}[/{s}]")


def display_error(message: str, hint: str | None = None) -> None:
    """Red-bordered panel with optional recovery hint."""
    body = f"[bold red]{ERROR} {message}[/bold red]"
    if hint:
        body += f"\n[dim]{hint}[/dim]"
    console.print(Panel(body, border_style="red", title="Error", title_align="left"))


def display_info(message: str) -> None:
    """Themed info message."""
    s = _c("info")
    console.print(f"[{s}]{INFO} {message}[/{s}]")
