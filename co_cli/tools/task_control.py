"""Agent tools for background task management.

All four tools follow the standard pattern: RunContext[CoDeps], return ToolReturn.
Background task state lives in ctx.deps.session.background_tasks (session-scoped, no disk I/O).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from opentelemetry import trace as otel_trace
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools.background import (
    BackgroundCleanupError,
    BackgroundTaskState,
    _make_task_id,
    kill_task,
    spawn_task,
)
from co_cli.tools.tool_output import tool_output


async def start_background_task(
    ctx: RunContext[CoDeps],
    command: str,
    description: str,
    working_directory: str | None = None,
) -> ToolReturn:
    """Start a shell command in the background without blocking the chat session.

    Use for long-running operations: test suites, batch processing, research scripts,
    bulk file modifications. Returns immediately with a task_id to track progress.

    The command runs in a subprocess with stdout+stderr captured in memory (last 500 lines).
    No interactive input is possible — commands that prompt for stdin will stall.

    Args:
        command: Shell command to run (e.g. "uv run pytest", "grep -r foo src/").
        description: Human-readable description of what this task does.
        working_directory: Working directory for the command. Defaults to cwd.
    """
    cwd = working_directory or str(Path.cwd())

    tracer = otel_trace.get_tracer("co-cli")
    with tracer.start_as_current_span("background_task_execute") as span:
        span.set_attribute("task.command", command)
        span.set_attribute("task.description", description)
        span.set_attribute("task.cwd", cwd)
        task_id = _make_task_id()
        state = BackgroundTaskState(
            task_id=task_id,
            command=command,
            cwd=cwd,
            description=description,
            status="running",
            started_at=datetime.now(UTC).isoformat(),
        )
        ctx.deps.session.background_tasks[task_id] = state
        try:
            await spawn_task(state, ctx.deps.session)
        except Exception as e:
            raise ModelRetry(f"Failed to start background task: {e}") from e

    display = f"[{task_id}] started — command: {command}"
    return tool_output(display, ctx=ctx, task_id=task_id, status="running")


async def check_task_status(
    ctx: RunContext[CoDeps],
    task_id: str,
    tail_lines: int = 20,
) -> ToolReturn:
    """Check the status and recent output of a background task.

    Returns status, duration, exit code, and the last N lines of output.

    Args:
        task_id: The task ID returned by start_background_task.
        tail_lines: Number of output lines to return (default 20).
    """
    state = ctx.deps.session.background_tasks.get(task_id)
    if state is None:
        return tool_output(
            f"Task not found: {task_id}",
            ctx=ctx,
            task_id=task_id,
            status="not_found",
            duration=None,
            exit_code=None,
            output_lines=[],
            is_binary=False,
        )

    output_lines = list(state.output_lines)[-tail_lines:]
    output_display = "\n".join(output_lines) if output_lines else "[no output yet]"

    duration: float | None = None
    if state.completed_at and state.started_at:
        try:
            started = datetime.fromisoformat(state.started_at)
            completed = datetime.fromisoformat(state.completed_at)
            duration = (completed - started).total_seconds()
        except Exception:
            pass

    dur_str = f"{duration:.1f}s" if duration is not None else "in progress"
    display = (
        f"Task {task_id}: {state.description}\n"
        f"  Started: {state.started_at}  Status: {state.status}  Exit: {state.exit_code}  Duration: {dur_str}\n"
        f"  Output (last {tail_lines} lines):\n{output_display}"
    )
    return tool_output(
        display,
        ctx=ctx,
        task_id=task_id,
        status=state.status,
        duration=duration,
        exit_code=state.exit_code,
        output_lines=output_lines,
        is_binary=False,
        description=state.description,
        started_at=state.started_at,
    )


async def cancel_background_task(
    ctx: RunContext[CoDeps],
    task_id: str,
) -> ToolReturn:
    """Cancel a running background task (sends SIGTERM, then SIGKILL after 200ms).

    No-op if the task has already completed, failed, or been cancelled.

    Args:
        task_id: The task ID to cancel.
    """
    state = ctx.deps.session.background_tasks.get(task_id)
    if state is None:
        return tool_output(
            f"Task not found: {task_id}", ctx=ctx, task_id=task_id, status="not_found"
        )

    if state.status != "running":
        return tool_output(
            f"Task already completed (status={state.status})",
            ctx=ctx,
            task_id=task_id,
            status=state.status,
        )

    try:
        await kill_task(state)
    except BackgroundCleanupError as e:
        return tool_output(
            f"Task {task_id} cancellation failed during cleanup: {e}",
            ctx=ctx,
            task_id=task_id,
            status="cancel_cleanup_failed",
            cleanup_incomplete=state.cleanup_incomplete,
            cleanup_error=state.cleanup_error,
        )
    return tool_output(f"Task {task_id} cancelled.", ctx=ctx, task_id=task_id, status="cancelled")


async def list_background_tasks(
    ctx: RunContext[CoDeps],
    status_filter: str | None = None,
) -> ToolReturn:
    """List background tasks, optionally filtered by status.

    Args:
        status_filter: Optional status to filter by: "running", "completed",
                       "failed", "cancelled". None = all tasks.
    """
    tasks_dict = ctx.deps.session.background_tasks
    if status_filter:
        tasks = [s for s in tasks_dict.values() if s.status == status_filter]
    else:
        tasks = list(tasks_dict.values())

    rows = [
        {
            "task_id": s.task_id,
            "status": s.status,
            "command": s.command,
            "started_at": s.started_at,
            "description": s.description,
        }
        for s in tasks
    ]

    if not rows:
        filter_note = f" with status={status_filter}" if status_filter else ""
        display = f"No background tasks{filter_note}."
    else:
        lines = [
            f"Background tasks ({len(rows)}{' — ' + status_filter if status_filter else ''}):\n"
        ]
        for r in rows:
            started = (r["started_at"] or "queued")[:19]
            desc_prefix = f"{r['description']}: " if r.get("description") else ""
            lines.append(
                f"  [{r['task_id']}] {r['status']} — {desc_prefix}{r['command']}  ({started})"
            )
        display = "\n".join(lines)

    return tool_output(display, ctx=ctx, tasks=rows, count=len(rows))
