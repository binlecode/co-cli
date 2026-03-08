"""Agent tools for background task management.

All four tools follow the standard pattern: RunContext[CoDeps], return dict[str, Any]
with a display field. Access TaskRunner via ctx.deps.services.task_runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext, ModelRetry

from co_cli.deps import CoDeps


async def start_background_task(
    ctx: RunContext[CoDeps],
    command: str,
    description: str,
    working_directory: str | None = None,
) -> dict[str, Any]:
    """Start a shell command in the background without blocking the chat session.

    Use for long-running operations: test suites, batch processing, research scripts,
    bulk file modifications. Returns immediately with a task_id to track progress.

    The command runs in a subprocess with stdout+stderr captured to disk.
    No interactive input is possible — commands that prompt for stdin will stall.

    Args:
        command: Shell command to run (e.g. "uv run pytest", "grep -r foo src/").
        description: Human-readable description of what this task does.
        working_directory: Working directory for the command. Defaults to cwd.
    """
    runner = ctx.deps.services.task_runner
    if runner is None:
        raise ModelRetry("Background task runner is not available in this session.")

    cwd = working_directory or str(Path.cwd())

    # Create OTel span for this background task
    tracer = otel_trace.get_tracer("co-cli")
    span_id: str | None = None
    with tracer.start_as_current_span("background_task_execute") as span:
        span.set_attribute("task.command", command)
        span.set_attribute("task.description", description)
        span.set_attribute("task.cwd", cwd)
        ctx_otel = otel_trace.get_current_span().get_span_context()
        if ctx_otel.is_valid:
            span_id = format(ctx_otel.span_id, "016x")

    approval_record = {"description": description, "command": command}
    try:
        task_id = await runner.start_task(command, cwd, approval_record, span_id)
    except Exception as e:
        raise ModelRetry(f"Failed to start background task: {e}")

    display = f"[{task_id}] started — command: {command}"
    return {
        "display": display,
        "task_id": task_id,
        "status": "running",
    }


async def check_task_status(
    ctx: RunContext[CoDeps],
    task_id: str,
    tail_lines: int = 20,
) -> dict[str, Any]:
    """Check the status and recent output of a background task.

    Returns status, duration, exit code, and the last N lines of output.
    Binary output is detected automatically and shown as a byte count instead.

    Args:
        task_id: The task ID returned by start_background_task.
        tail_lines: Number of output lines to return (default 20).
    """
    runner = ctx.deps.services.task_runner
    if runner is None:
        raise ModelRetry("Background task runner is not available in this session.")

    meta = runner.get_task(task_id)
    if meta is None:
        return {
            "display": f"Task not found: {task_id}",
            "task_id": task_id,
            "status": "not_found",
            "duration": None,
            "exit_code": None,
            "output_lines": [],
            "is_binary": False,
        }

    status = meta.get("status", "unknown")
    exit_code = meta.get("exit_code")
    is_binary = meta.get("is_binary", False)

    # Read duration from result.json when available
    duration: float | None = None
    result_path = runner._storage.result_path(task_id)
    if result_path.exists():
        try:
            import json
            result = json.loads(result_path.read_text())
            duration = result.get("duration")
        except Exception:
            pass

    output_path = runner._storage.output_path(task_id)
    if not output_path.exists():
        output_display = "[output log not found]"
        output_lines = []
    elif is_binary:
        size = runner._storage.output_size(task_id)
        output_display = f"[binary output — {size} bytes]"
        output_lines = [output_display]
    else:
        lines = runner._storage.tail_output(task_id, n=tail_lines)
        output_lines = lines
        output_display = "\n".join(lines) if lines else "[no output yet]"

    dur_str = f"{duration:.1f}s" if duration is not None else "in progress"
    display = (
        f"Task {task_id}\n"
        f"  Status: {status}  Exit: {exit_code}  Duration: {dur_str}\n"
        f"  Output (last {tail_lines} lines):\n{output_display}"
    )

    return {
        "display": display,
        "task_id": task_id,
        "status": status,
        "duration": duration,
        "exit_code": exit_code,
        "output_lines": output_lines,
        "is_binary": is_binary,
    }


async def cancel_background_task(
    ctx: RunContext[CoDeps],
    task_id: str,
) -> dict[str, Any]:
    """Cancel a running background task (sends SIGTERM, then SIGKILL after 200ms).

    No-op if the task has already completed, failed, or been cancelled.

    Args:
        task_id: The task ID to cancel.
    """
    runner = ctx.deps.services.task_runner
    if runner is None:
        raise ModelRetry("Background task runner is not available in this session.")

    meta = runner.get_task(task_id)
    if meta is None:
        return {"display": f"Task not found: {task_id}", "task_id": task_id, "status": "not_found"}

    status = meta.get("status")
    from co_cli._background import TaskStatus
    if status != TaskStatus.running.value:
        return {
            "display": f"Task already completed (status={status})",
            "task_id": task_id,
            "status": status,
        }

    cancelled = await runner.cancel_task(task_id)
    if cancelled:
        return {"display": f"Task {task_id} cancelled.", "task_id": task_id, "status": "cancelled"}
    return {
        "display": f"Task {task_id} was not running (status={status})",
        "task_id": task_id,
        "status": status,
    }


async def list_background_tasks(
    ctx: RunContext[CoDeps],
    status_filter: str | None = None,
) -> dict[str, Any]:
    """List background tasks, optionally filtered by status.

    Args:
        status_filter: Optional status to filter by: "pending", "running",
                       "completed", "failed", "cancelled". None = all tasks.
    """
    runner = ctx.deps.services.task_runner
    if runner is None:
        raise ModelRetry("Background task runner is not available in this session.")

    tasks = runner.list_tasks(status_filter)
    rows = [
        {
            "task_id": t.get("task_id"),
            "status": t.get("status"),
            "command": t.get("command"),
            "started_at": t.get("started_at"),
        }
        for t in tasks
    ]

    if not rows:
        filter_note = f" with status={status_filter}" if status_filter else ""
        display = f"No background tasks{filter_note}."
    else:
        lines = [f"Background tasks ({len(rows)}{' — ' + status_filter if status_filter else ''}):\n"]
        for r in rows:
            started = (r["started_at"] or "queued")[:19]
            lines.append(f"  [{r['task_id']}] {r['status']} — {r['command']}  ({started})")
        display = "\n".join(lines)

    return {"display": display, "tasks": rows, "count": len(rows)}
