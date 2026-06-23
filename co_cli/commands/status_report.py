"""Shared current-state reporting primitives.

Deps-derived status computations shared by the welcome banner (bootstrap.banner)
and the /status command (commands.status), kept here as the single source of
truth so the two surfaces cannot diverge. These read tool/skill/command catalogs,
the dream daemon filesystem state, and the live context estimate from ``deps`` —
the owning concern is current-state reporting, not bootstrap wiring, so both
consumers import this downward.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

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

    None only when the context window is unknown. Before the first turn there is
    no per-request estimate yet, but the static floor (system prompt + tool/skill
    schemas) is always resident — so a live session reports that baseline, never None.
    Shared by the footer snapshot and the /status report so the calc cannot drift.
    """
    if deps.model_max_context_tokens <= 0:
        return None
    estimate = deps.runtime.current_request_tokens_estimate
    if estimate is None:
        estimate = deps.static_floor_tokens
    return estimate / deps.model_max_context_tokens


@dataclass(frozen=True)
class DreamStatus:
    """Live dream-daemon runtime state — the single source shared by the banner and
    /status. Reflects the actual daemon (running or not) so neither surface can
    disagree with /dream. ``dream.autostart`` is config (whether the REPL spawns one
    on launch), not runtime, and is deliberately absent here.
    """

    running: bool
    queue_depth: int
    last_housekeeping_at: str | None


def dream_status() -> DreamStatus:
    """Probe the dream daemon once: running / queue depth / last pass.

    The one place the daemon's filesystem status is interpreted, so the banner and
    /status can't disagree with the actual daemon (or with /dream). Reads the live
    pidfile — the daemon is shared per CO_HOME, so one started by any session (or via
    ``/dream start``) shows as running regardless of this session's ``dream.autostart``.
    """
    from co_cli.config.core import DREAM_DAEMON_DIR, USER_DIR
    from co_cli.daemons.dream.process import status_daemon
    from co_cli.daemons.dream.state import load_housekeeping_state

    status = status_daemon(USER_DIR)
    return DreamStatus(
        running=bool(status.get("running")),
        queue_depth=status.get("queue_depth", 0),
        last_housekeeping_at=load_housekeeping_state(DREAM_DAEMON_DIR).last_housekeeping_at,
    )
