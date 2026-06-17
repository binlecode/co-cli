"""Slash command handler for /status — consolidated current-state snapshot.

Read-only: assembles a sectioned report from in-memory ``deps`` plus cheap local
filesystem reads (dream daemon state, the usage ledger). Makes no model call and
writes nothing. Each section is gathered under a guard so a missing or unreadable
source degrades that section to a placeholder rather than aborting the whole
report — the command always prints something.

History stays in /usage, /history, and /sessions; /status is current-state only.
co tracks token usage, not cost, so the Model section reports session token
totals (matching /usage), never a dollar figure.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from co_cli.display.core import console, make_table

if TYPE_CHECKING:
    from co_cli.commands.types import CommandContext

_PLACEHOLDER = "—"


def _safe(fn: Callable[[], str], default: str = _PLACEHOLDER) -> str:
    """Run a value thunk, degrading to a placeholder on any failure."""
    try:
        return fn()
    except Exception:
        return default


async def _cmd_status(ctx: CommandContext, args: str) -> None:
    """Show a consolidated current-state report.

    Sections: Session, Model & context, Dream, Work in flight, Capabilities, and
    any degraded flags. Takes no subcommands — ``args`` is ignored (mirrors the
    /dream peer). Read-only: never writes state or enqueues a model request.
    """
    sections: list[tuple[str, list[tuple[str, str]]]] = [
        ("Session", _session_rows(ctx)),
        ("Model & context", _model_rows(ctx)),
        ("Dream", _dream_rows(ctx)),
        ("Work in flight", _work_rows(ctx)),
        ("Capabilities", _capability_rows(ctx)),
        ("Degraded", _degraded_rows(ctx)),
    ]

    console.print("[bold]Status[/bold]")
    for title, rows in sections:
        console.print(f"[info]{title}[/info]")
        table = make_table("field", "value")
        for label, value in rows:
            table.add_row(f"  {label}", value)
        console.print(table)


def _session_rows(ctx: CommandContext) -> list[tuple[str, str]]:
    from co_cli.commands.status_report import workspace_dir_label
    from co_cli.project_info import project_info

    deps = ctx.deps

    def _dir() -> str:
        label = workspace_dir_label(deps)
        branch = project_info().git_branch
        return f"{label}  ({branch})" if branch else label

    return [
        ("id", _safe(lambda: deps.session.session_path.stem[-8:] or _PLACEHOLDER)),
        ("dir", _safe(_dir)),
        ("personality", _safe(lambda: deps.config.personality or "disabled")),
        ("mode", _safe(lambda: _mode(ctx))),
    ]


def _mode(ctx: CommandContext) -> str:
    tasks = ctx.deps.session.background_tasks
    running = sum(1 for t in tasks.values() if getattr(t, "status", None) == "running")
    return f"active ({running} background)" if running else "idle"


def _model_rows(ctx: CommandContext) -> list[tuple[str, str]]:
    deps = ctx.deps

    def _model() -> str:
        llm = deps.config.llm
        return f"{llm.provider} / {llm.model}" if llm.model else llm.provider

    def _context() -> str:
        from co_cli.commands.status_report import context_pct

        pct = context_pct(deps)
        if pct is None:
            return _PLACEHOLDER
        estimate = deps.runtime.current_request_tokens_estimate
        if estimate is None:
            estimate = deps.static_floor_tokens
        return f"{pct:.0%}  ({estimate:,} / {deps.model_max_context_tokens:,} tokens)"

    def _session_tokens() -> str:
        from co_cli.session.usage import ORIGIN_SESSION, aggregate

        session_id = deps.session.session_path.stem[-8:]
        window = aggregate(deps.usage_log_path, session_id=session_id, origin=ORIGIN_SESSION)
        totals = window.session
        return f"{totals.total:,}  ({totals.input_tokens:,} in / {totals.output_tokens:,} out)"

    return [
        ("model", _safe(_model)),
        ("context", _safe(_context)),
        ("session tokens", _safe(_session_tokens)),
    ]


def _dream_rows(ctx: CommandContext) -> list[tuple[str, str]]:
    def _rows() -> list[tuple[str, str]]:
        from co_cli.commands.status_report import dream_status

        state = dream_status(ctx.deps)
        if not state.enabled:
            return [("state", "disabled")]
        state_label = "running" if state.running else "enabled but not running"
        return [
            ("state", state_label),
            ("queue depth", str(state.queue_depth)),
            ("last housekeeping", state.last_housekeeping_at or "never"),
        ]

    try:
        return _rows()
    except Exception:
        return [("state", _PLACEHOLDER)]


def _work_rows(ctx: CommandContext) -> list[tuple[str, str]]:
    deps = ctx.deps

    def _background() -> str:
        tasks = deps.session.background_tasks
        running = sum(1 for t in tasks.values() if getattr(t, "status", None) == "running")
        return f"{running} running / {len(tasks)} total"

    def _queue_depth() -> str:
        return str(len(ctx.input_queue) if ctx.input_queue is not None else 0)

    return [
        ("background tasks", _safe(_background)),
        ("pending approvals", _safe(lambda: str(len(deps.session.session_approval_rules)))),
        ("input queue", _safe(_queue_depth)),
    ]


def _capability_rows(ctx: CommandContext) -> list[tuple[str, str]]:
    from co_cli.commands.status_report import build_status_counts

    deps = ctx.deps

    def _memory() -> str:
        backend = deps.config.memory.search_backend
        if backend == "grep":
            return "grep (no index)"
        memory_count = deps.memory_store.count() if deps.memory_store is not None else 0
        session_count = deps.session_store.count() if deps.session_store is not None else 0
        return f"{backend}  (memory: {memory_count}  sessions: {session_count})"

    def _counts() -> str:
        counts = build_status_counts(deps)
        return (
            f"tools: {counts.tools}  skills: {counts.skills}  "
            f"mcp: {counts.mcp}  commands: {counts.commands}"
        )

    return [
        ("memory", _safe(_memory)),
        ("registered", _safe(_counts)),
    ]


def _degraded_rows(ctx: CommandContext) -> list[tuple[str, str]]:
    def _flags() -> str:
        degradations = ctx.deps.degradations
        if not degradations:
            return "none"
        return ", ".join(f"{key}: {value}" for key, value in degradations.items())

    return [("flags", _safe(_flags))]
