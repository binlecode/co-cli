"""Welcome banner display for the Co CLI chat startup sequence."""

import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.config import ROLE_REASONING
from co_cli.display._core import console

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


def display_welcome_banner(deps: "CoDeps") -> None:
    """Render welcome banner with ASCII art, model, and environment info."""
    from rich.panel import Panel

    config = deps.config
    art = "\n".join(_ASCII_ART.get(config.theme, _ASCII_ART["light"]))

    version = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]

    reasoning_entry = config.role_models.get(ROLE_REASONING)
    if reasoning_entry:
        llm_provider = f"{config.llm_provider} / {reasoning_entry.model}"
    else:
        llm_provider = config.llm_provider

    from co_cli.commands._commands import BUILTIN_COMMANDS, get_skill_registry
    tool_count = len(deps.tool_index)
    skill_count = len(get_skill_registry(deps.skill_commands))
    mcp_count = len(deps.config.mcp_servers or {})
    cmd_count = len(BUILTIN_COMMANDS) + sum(1 for s in deps.skill_commands.values() if s.user_invocable)

    try:
        git_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        git_branch = ""

    backend = deps.config.knowledge_search_backend
    knowledge_degradation = deps.config.degradations.get("knowledge")

    if backend == "hybrid":
        knowledge_info = (
            f"hybrid · {deps.config.knowledge_embedding_provider}/"
            f"{deps.config.knowledge_embedding_model} {deps.config.knowledge_embedding_dims}d"
        )
    elif backend == "fts5":
        knowledge_info = "fts5"
    else:
        knowledge_info = "grep (no index)"

    knowledge_line = f"    Knowledge: [accent]{knowledge_info}[/accent]"
    if knowledge_degradation:
        knowledge_line += f"  [yellow]({knowledge_degradation})[/yellow]"

    lines = [
        f"\n[accent]{art}[/accent]\n",
        f"    v{version} — CLI Assistant",
        f"    Model: [accent]{llm_provider}[/accent]",
        knowledge_line,
        f"    Tools: {tool_count}  Skills: {skill_count}  MCP: {mcp_count}  Commands: {cmd_count}",
        f"    Dir: {Path.cwd().name}" + (f"  ({git_branch})" if git_branch else ""),
        "",
        f"    [success]✓ Ready{'  (degraded)' if deps.config.degradations else ''}[/success]",
        f"    [dim]Type /help for commands, 'exit' to quit[/dim]",
    ]
    console.print(Panel("\n".join(lines), border_style="accent", expand=False))
