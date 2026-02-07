"""Themed terminal display — console, semantic styles, display helpers."""

from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

from co_cli.config import settings

# -- Theme palettes (keyed by theme name) ------------------------------------

_THEMES: dict[str, dict[str, str]] = {
    "dark":  {"status": "yellow",      "info": "cyan", "accent": "bold cyan",  "yolo": "bold orange3", "shell": "dim"},
    "light": {"status": "dark_orange", "info": "blue", "accent": "bold blue",  "yolo": "bold orange3", "shell": "dim"},
}

# -- Console (single instance, themed) --------------------------------------

console = Console(theme=Theme(_THEMES.get(settings.theme, _THEMES["light"])))

# -- Indicators ------------------------------------------------------------

PROMPT_CHAR = "❯"
BULLET      = "▸"
SUCCESS     = "✦"
ERROR       = "✖"
INFO        = "◈"

# -- Display helpers -------------------------------------------------------


def display_status(message: str, style: str | None = None) -> None:
    """Themed bullet + message."""
    s = style or "status"
    console.print(f"[{s}]{BULLET} {message}[/{s}]")


def display_error(message: str, hint: str | None = None) -> None:
    """Red-bordered panel with optional recovery hint."""
    body = f"[bold red]{ERROR} {message}[/bold red]"
    if hint:
        body += f"\n[dim]{hint}[/dim]"
    console.print(Panel(body, border_style="red", title="Error", title_align="left"))


def display_info(message: str) -> None:
    """Themed info message."""
    console.print(f"[info]{INFO} {message}[/info]")
