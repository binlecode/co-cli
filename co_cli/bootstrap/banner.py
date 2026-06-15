"""Welcome banner display for the Co CLI chat startup sequence."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from co_cli.bootstrap.project_info import project_info
from co_cli.display.core import console

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


@dataclass(frozen=True)
class StatusCounts:
    """Registered-capability counts shared by the banner and the /status report."""

    tools: int
    skills: int
    mcp: int
    commands: int


def build_status_counts(deps: "CoDeps") -> StatusCounts:
    """Count registered tools, skills, MCP servers, and commands.

    The command count is builtins plus user-invocable skills — the same formula
    the banner has always used, kept here as the single source of truth so the
    banner and /status cannot diverge.
    """
    from co_cli.commands.registry import BUILTIN_COMMANDS
    from co_cli.skills.index import get_skill_catalog

    return StatusCounts(
        tools=len(deps.tool_catalog),
        skills=len(get_skill_catalog(deps.skill_catalog)),
        mcp=len(deps.config.mcp_servers or {}),
        commands=len(BUILTIN_COMMANDS)
        + sum(1 for s in deps.skill_catalog.values() if s.user_invocable),
    )


def workspace_dir_label(deps: "CoDeps") -> str:
    """The workspace directory label: full path when configured, else bare name."""
    return str(deps.workspace_dir) if deps.config.workspace_path else deps.workspace_dir.name


def context_pct(deps: "CoDeps") -> float | None:
    """Fraction of the model context window currently estimated in use.

    None when no estimate exists yet (between turns) or the budget is unknown.
    Shared by the footer snapshot and the /status report so the calc cannot drift.
    """
    estimate = deps.runtime.current_request_tokens_estimate
    if estimate is not None and deps.model_max_ctx > 0:
        return estimate / deps.model_max_ctx
    return None


@dataclass(frozen=True)
class DreamStatus:
    """Interpreted dream-daemon state — the single source for the three-branch
    reading (disabled / running / enabled-but-not-running) shared by the banner
    and /status. Surfaces format their own wording from these fields.
    """

    enabled: bool
    running: bool
    queue_depth: int
    last_housekeeping_at: str | None


def dream_status(deps: "CoDeps") -> DreamStatus:
    """Probe the dream daemon once: enabled / running / queue depth / last pass.

    The one place the daemon's filesystem status is interpreted, so the banner and
    /status can't disagree on whether it is running. When disabled, returns without
    touching the daemon filesystem (mirrors the banner's prior early-return).
    """
    if not deps.config.dream.enabled:
        return DreamStatus(enabled=False, running=False, queue_depth=0, last_housekeeping_at=None)

    from co_cli.config.core import DREAM_DAEMON_DIR, USER_DIR
    from co_cli.daemons.dream._state import load_housekeeping_state
    from co_cli.daemons.dream.process import status_daemon

    status = status_daemon(USER_DIR)
    return DreamStatus(
        enabled=True,
        running=bool(status.get("running")),
        queue_depth=status.get("queue_depth", 0),
        last_housekeeping_at=load_housekeeping_state(DREAM_DAEMON_DIR).last_housekeeping_at,
    )


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
        f"    v{info.version} — CLI Assistant",
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
