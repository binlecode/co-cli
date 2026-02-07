"""ASCII art welcome banner for the REPL."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.panel import Panel

from co_cli.config import settings
from co_cli.display import console, _c

if TYPE_CHECKING:
    from co_cli.status import StatusInfo

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


def display_welcome_banner(info: StatusInfo) -> None:
    """Render welcome banner with ASCII art, model, and environment info."""
    accent = _c("accent")
    art = "\n".join(ASCII_ART.get(settings.theme, ASCII_ART["light"]))

    lines = [
        f"\n[{accent}]{art}[/{accent}]\n",
        f"    v{info.version} — CLI Assistant",
        f"    Model: [{accent}]{info.llm_provider}[/{accent}]",
        f"    Tools: {info.tool_count}  Sandbox: {info.docker}",
        f"    Dir: {info.cwd}" + (f"  ({info.git_branch})" if info.git_branch else ""),
        "",
        f"    [dim]Type 'exit' to quit[/dim]",
    ]
    console.print(Panel("\n".join(lines), border_style=accent, expand=False))
