"""Welcome banner display for the Co CLI chat startup sequence."""

from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.bootstrap.project_info import project_info
from co_cli.display._core import console

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

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

    info = project_info()

    if config.llm.model:
        llm_provider = f"{config.llm.provider} / {config.llm.model}"
    else:
        llm_provider = config.llm.provider

    from co_cli.commands._commands import BUILTIN_COMMANDS, get_skill_registry

    tool_count = len(deps.tool_index)
    skill_count = len(get_skill_registry(deps.skill_commands))
    mcp_count = len(deps.config.mcp_servers or {})
    cmd_count = len(BUILTIN_COMMANDS) + sum(
        1 for s in deps.skill_commands.values() if s.user_invocable
    )

    backend = deps.config.knowledge.search_backend
    knowledge_degradation = deps.degradations.get("knowledge")

    if backend == "hybrid":
        knowledge_info = (
            f"hybrid · {deps.config.knowledge.embedding_provider}/"
            f"{deps.config.knowledge.embedding_model} {deps.config.knowledge.embedding_dims}d"
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
        f"    v{info.version} — CLI Assistant",
        f"    Model: [accent]{llm_provider}[/accent]",
        knowledge_line,
        f"    Tools: {tool_count}  Skills: {skill_count}  MCP: {mcp_count}  Commands: {cmd_count}",
        f"    Dir: {Path.cwd().name}" + (f"  ({info.git_branch})" if info.git_branch else ""),
        "",
        f"    [success]✓ Ready{'  (degraded)' if deps.degradations else ''}[/success]",
        "    [dim]Type /help for commands, 'exit' to quit[/dim]",
    ]
    console.print(Panel("\n".join(lines), border_style="accent", expand=False))
