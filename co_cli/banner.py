"""ASCII art welcome banner for the REPL."""

from importlib.metadata import version as _pkg_version

from rich.panel import Panel

from co_cli.config import settings
from co_cli.display import console, _c

VERSION = _pkg_version("co-cli")

ASCII_ART = {
    "dark": [
        "    █▀▀ █▀█   █▀▀ █   █",
        "    █▄▄ █▄█   █▄▄ █▄▄ █",
    ],
    "light": [
        "    ┌─┐ ┌─┐   ┌─┐ ┬   ┬",
        "    │   │ │   │   │   │",
        "    └─┘ └─┘   └─┘ └─┘ ┴",
    ],
}


def display_welcome_banner(model_info: str, version: str = VERSION) -> None:
    """Render welcome banner with ASCII art and model info."""
    accent = _c("accent")
    art = "\n".join(ASCII_ART.get(settings.theme, ASCII_ART["light"]))
    body = (
        f"\n[{accent}]{art}[/{accent}]\n\n"
        f"    v{version} — CLI Assistant\n\n"
        f"    Model: [{accent}]{model_info}[/{accent}]\n\n"
        f"    [dim]Type 'exit' to quit[/dim]\n"
    )
    console.print(Panel(body, border_style=accent, expand=False))
