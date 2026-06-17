"""Welcome banner display for the Co CLI chat startup sequence."""

from typing import TYPE_CHECKING

from co_cli.commands.status_report import (
    build_status_counts,
    dream_status,
    workspace_dir_label,
)
from co_cli.display.core import console
from co_cli.project_info import project_info

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


def build_dream_line(deps: "CoDeps") -> str:
    """Build the Dream: status line for the welcome banner."""
    state = dream_status(deps)
    if not state.enabled:
        return "    Dream: [dim]disabled[/dim]"
    if state.running:
        return f"    Dream: [accent]✓ running[/accent]  queue: {state.queue_depth}"
    return (
        f"    Dream: [yellow]enabled but daemon not running[/yellow]  "
        f"queue: {state.queue_depth} (on disk)"
    )


def build_memory_line(
    *,
    backend: str,
    backend_label: str,
    memory_degradation: str | None,
    memory_count: int,
    session_count: int,
) -> str:
    """Build the Memory: status line for the welcome banner.

    When the active item count exceeds ``MEMORY_ITEM_COUNT_WARN``, the count is
    flagged yellow — mirroring the warn-only housekeeping tripwire (memory is
    never auto-evicted; crossing the threshold signals a write loop / runaway /
    pollution to investigate). See [dream.md](../../docs/specs/dream.md) §2.4.
    """
    from co_cli.config.memory import MEMORY_ITEM_COUNT_WARN

    line = f"    Memory: [accent]{backend_label}[/accent]"
    if memory_degradation:
        line += f"  [yellow]({memory_degradation})[/yellow]"
    if backend != "grep":
        if memory_count > MEMORY_ITEM_COUNT_WARN:
            line += f"  [yellow]⚠ memory: {memory_count} (over count tripwire)[/yellow]"
        else:
            line += f"  memory: {memory_count}"
        line += f"  sessions: {session_count}"
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

    counts = build_status_counts(deps)

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

    dream_line = build_dream_line(deps)

    lines = [
        f"\n[accent]{art}[/accent]\n",
        f"    v{info.version} — Personal AI Agent",
        f"    Model: [accent]{llm_provider}[/accent]",
        memory_line,
        dream_line,
        f"    Tools: {counts.tools}  Skills: {counts.skills}  MCP: {counts.mcp}  Commands: {counts.commands}",
        f"    Dir: {workspace_dir_label(deps)}"
        + (f"  ({info.git_branch})" if info.git_branch else ""),
        "",
        f"    [success]✓ Ready{'  (degraded)' if deps.degradations else ''}[/success]",
        "    [dim]Type /help for commands, 'exit' to quit[/dim]",
    ]
    console.print(Panel("\n".join(lines), border_style="accent", expand=False))
