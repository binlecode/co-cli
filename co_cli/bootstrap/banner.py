"""Welcome banner display for the Co CLI chat startup sequence."""

from typing import TYPE_CHECKING

from co_cli.commands.status_report import (
    build_status_counts,
    dream_status,
    workspace_dir_label,
)
from co_cli.display.core import active_theme_name, console, glyphs
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


# Widest label ("Memory") sets the shared column so every row's value aligns.
_LABEL_WIDTH = 6


def _row(label: str, value: str) -> str:
    """One aligned banner row: a dim label padded to the shared column, then value.

    Labels recede (dim) and values stay plain so the eye rests on the data; the
    logo is the sole accent. ` · ` is the single intra-row separator (matching the
    status-line convention). See [tui.md](../../docs/specs/tui.md) §4.
    """
    return f"    [hint]{label:<{_LABEL_WIDTH}}[/hint]  {value}"


def build_dream_line() -> str:
    """Build the aligned Dream row — live runtime state only (running / not)."""
    state = dream_status()
    if state.running:
        return _row("Dream", f"[success]running[/success] · queue {state.queue_depth}")
    return _row("Dream", f"[dim]not running[/dim] · queue {state.queue_depth} (on disk)")


def build_memory_line(
    *,
    backend: str,
    backend_label: str,
    memory_degradation: str | None,
    memory_count: int,
    session_count: int,
) -> str:
    """Build the aligned Memory row for the welcome banner.

    When the active item count exceeds ``MEMORY_ITEM_COUNT_WARN``, the count is
    flagged yellow — mirroring the warn-only housekeeping tripwire (memory is
    never auto-evicted; crossing the threshold signals a write loop / runaway /
    pollution to investigate). See [dream.md](../../docs/specs/dream.md) §2.4.
    """
    from co_cli.config.memory import MEMORY_ITEM_COUNT_WARN

    value = backend_label
    if memory_degradation:
        value += f" · [yellow]({memory_degradation})[/yellow]"
    if backend != "grep":
        if memory_count > MEMORY_ITEM_COUNT_WARN:
            value += (
                f" · [yellow]{glyphs().warning} {memory_count} mem (over count tripwire)[/yellow]"
            )
        else:
            value += f" · {memory_count} mem"
        value += f" · {session_count} sess"
    return _row("Memory", value)


def display_welcome_banner(
    deps: "CoDeps", *, memory_count: int = 0, session_count: int = 0
) -> None:
    """Render welcome banner with ASCII art, model, and environment info."""
    from rich.panel import Panel

    config = deps.config
    art = "\n".join(_ASCII_ART.get(active_theme_name(), _ASCII_ART["light"]))

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

    caps_value = (
        f"{counts.tools} · {counts.skills} skills · {counts.mcp} mcp · {counts.commands} cmds"
    )
    dir_value = workspace_dir_label(deps)
    if info.git_branch:
        dir_value += f" · {info.git_branch}"

    lines = [
        f"[accent]{art}[/accent]",
        "",
        f"    v{info.version} [hint]— Personal AI Agent[/hint]",
        _row("Model", llm_provider),
        memory_line,
        build_dream_line(),
        _row("Tools", caps_value),
        _row("Dir", dir_value),
    ]
    if deps.degradations:
        degraded = ", ".join(sorted(deps.degradations))
        lines.append(f"    [warning]{glyphs().warning} degraded: {degraded}[/warning]")
    lines.append("    [hint]Type /help for commands, 'exit' to quit[/hint]")

    console.print(Panel("\n".join(lines), border_style="dim", expand=False))
