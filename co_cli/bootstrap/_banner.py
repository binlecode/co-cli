"""Welcome banner display for the Co CLI chat startup sequence."""

import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.config import ROLE_REASONING
from co_cli.deps import CoConfig
from co_cli.display import console

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


_PYPROJECT = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"

_ASCII_ART = {
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


def display_welcome_banner(deps: "CoDeps", config: CoConfig) -> None:
    """Render welcome banner with ASCII art, model, and environment info."""
    from rich.panel import Panel

    art = "\n".join(_ASCII_ART.get(config.theme, _ASCII_ART["light"]))

    version = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]

    reasoning_entry = config.role_models.get(ROLE_REASONING)
    if reasoning_entry:
        llm_provider = f"{config.llm_provider} / {reasoning_entry.model}"
    else:
        llm_provider = config.llm_provider

    from co_cli.commands._commands import BUILTIN_COMMANDS
    tool_count = len(deps.session.tool_names)
    skill_count = len(deps.session.skill_registry)
    mcp_count = len(deps.config.mcp_servers or {})
    cmd_count = len(BUILTIN_COMMANDS) + deps.session.slash_command_count

    try:
        git_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        git_branch = ""

    lines = [
        f"\n[accent]{art}[/accent]\n",
        f"    v{version} — CLI Assistant",
        f"    Model: [accent]{llm_provider}[/accent]",
        f"    Tools: {tool_count}  Skills: {skill_count}  MCP: {mcp_count}  Commands: {cmd_count}",
        f"    Dir: {Path.cwd().name}" + (f"  ({git_branch})" if git_branch else ""),
        "",
        f"    [success]✓ Ready[/success]",
        f"    [dim]Type /help for commands, 'exit' to quit[/dim]",
    ]
    console.print(Panel("\n".join(lines), border_style="accent", expand=False))
