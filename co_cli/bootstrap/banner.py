"""Welcome banner display for the Co CLI chat startup sequence."""

from typing import TYPE_CHECKING

from co_cli.bootstrap.project_info import project_info
from co_cli.display.core import console

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


def build_memory_line(
    *,
    backend: str,
    backend_label: str,
    memory_degradation: str | None,
    memory_count: int,
    session_count: int,
) -> str:
    """Build the Memory: status line for the welcome banner."""
    line = f"    Memory: [accent]{backend_label}[/accent]"
    if memory_degradation:
        line += f"  [yellow]({memory_degradation})[/yellow]"
    if backend != "grep":
        line += f"  memory: {memory_count}  sessions: {session_count}"
    return line


def display_welcome_banner(
    deps: "CoDeps", *, memory_count: int = 0, session_count: int = 0
) -> None:
    """Render welcome banner with ASCII art, model, and environment info."""
    from rich.panel import Panel

    config = deps.config
    art = "\n".join(_ASCII_ART.get(config.theme, _ASCII_ART["light"]))

    info = project_info()

    if config.llm.model:
        llm_provider = f"{config.llm.provider} / {config.llm.model}"
    else:
        llm_provider = config.llm.provider

    from co_cli.commands.registry import BUILTIN_COMMANDS
    from co_cli.skills.index import get_skill_index

    tool_count = len(deps.tool_index)
    skill_count = len(get_skill_index(deps.skill_index))
    mcp_count = len(deps.config.mcp_servers or {})
    cmd_count = len(BUILTIN_COMMANDS) + sum(
        1 for s in deps.skill_index.values() if s.user_invocable
    )

    backend = deps.config.memory.search_backend
    memory_degradation = deps.degradations.get("memory")

    if backend == "hybrid":
        backend_label = (
            f"hybrid · {deps.config.memory.embedding_provider}/"
            f"{deps.config.memory.embedding_model} {deps.config.memory.embedding_dims}d"
        )
    elif backend == "fts5":
        backend_label = "fts5"
    else:
        backend_label = "grep (no index)"

    memory_line = build_memory_line(
        backend=backend,
        backend_label=backend_label,
        memory_degradation=memory_degradation,
        memory_count=memory_count,
        session_count=session_count,
    )

    lines = [
        f"\n[accent]{art}[/accent]\n",
        f"    v{info.version} — CLI Assistant",
        f"    Model: [accent]{llm_provider}[/accent]",
        memory_line,
        f"    Tools: {tool_count}  Skills: {skill_count}  MCP: {mcp_count}  Commands: {cmd_count}",
        f"    Dir: {str(deps.workspace_dir) if deps.config.workspace_path else deps.workspace_dir.name}"
        + (f"  ({info.git_branch})" if info.git_branch else ""),
        "",
        f"    [success]✓ Ready{'  (degraded)' if deps.degradations else ''}[/success]",
        "    [dim]Type /help for commands, 'exit' to quit[/dim]",
    ]
    console.print(Panel("\n".join(lines), border_style="accent", expand=False))
