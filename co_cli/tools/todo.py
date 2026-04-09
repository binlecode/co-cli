"""Session-scoped task tracking tools.

Provides a lightweight todo list the model uses to track sub-goals within a
session. State lives in CoDeps.session.session_todos — it is not persisted to disk and
is cleared when the session ends.

Pattern follows OpenCode (session/todo.ts) and Claude Code (TodoWrite tool):
write_todos replaces the entire list (model rewrites to update), read_todos
surfaces current state for completeness verification.
"""

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps
from co_cli.tools.tool_output import tool_output

# Valid status and priority values
_VALID_STATUS = {"pending", "in_progress", "completed", "cancelled"}
_VALID_PRIORITY = {"high", "medium", "low"}


def write_todos(
    ctx: RunContext[CoDeps],
    todos: list[dict[str, Any]],
) -> ToolReturn:
    """Replace the session todo list with the provided items.

    Call this to create or update the task list for a multi-step directive.
    Rewrite the full list to update any item's status or content — this is
    idempotent and safe to call multiple times as work progresses.

    Use before starting a multi-step task, then update status fields as each
    sub-goal completes. Before ending a turn, call read_todos to verify no
    pending or in_progress items remain.

    Each item must have:
    - content (str): task description
    - status (str): "pending" | "in_progress" | "completed" | "cancelled"
    - priority (str, optional): "high" | "medium" | "low" — defaults to "medium"

    Returns a dict with:
    - display: summary of the written todo list — show directly to user
    - count: number of items written
    - pending: number of pending items
    - in_progress: number of in_progress items

    Args:
        todos: Full task list to store. Replaces any existing session todos.
    """
    validated: list[dict[str, Any]] = []
    errors: list[str] = []

    for i, item in enumerate(todos):
        if not isinstance(item, dict):
            errors.append(f"Item {i}: expected dict, got {type(item).__name__}")
            continue

        content = item.get("content", "").strip()
        if not content:
            errors.append(f"Item {i}: missing or empty 'content'")
            continue

        status = item.get("status", "pending")
        if status not in _VALID_STATUS:
            errors.append(
                f"Item {i}: invalid status '{status}' — must be one of {sorted(_VALID_STATUS)}"
            )
            continue

        priority = item.get("priority", "medium")
        if priority not in _VALID_PRIORITY:
            errors.append(
                f"Item {i}: invalid priority '{priority}' — "
                f"must be one of {sorted(_VALID_PRIORITY)}"
            )
            continue

        validated.append({"content": content, "status": status, "priority": priority})

    if errors:
        return tool_output(
            "Todo list NOT saved — validation errors:\n" + "\n".join(f"  - {e}" for e in errors),
            ctx=ctx,
            count=0,
            pending=0,
            in_progress=0,
            errors=errors,
        )

    ctx.deps.session.session_todos = validated

    pending = sum(1 for t in validated if t["status"] == "pending")
    in_progress = sum(1 for t in validated if t["status"] == "in_progress")
    completed = sum(1 for t in validated if t["status"] == "completed")
    cancelled = sum(1 for t in validated if t["status"] == "cancelled")

    lines = [f"Todo list saved ({len(validated)} items):\n"]
    status_icon = {
        "pending": "○",
        "in_progress": "◐",
        "completed": "●",
        "cancelled": "✗",
    }
    for t in validated:
        icon = status_icon.get(t["status"], "?")
        priority_str = f" [{t['priority']}]" if t["priority"] != "medium" else ""
        lines.append(f"  {icon} {t['content']}{priority_str}  ({t['status']})")

    summary_parts = []
    if pending:
        summary_parts.append(f"{pending} pending")
    if in_progress:
        summary_parts.append(f"{in_progress} in_progress")
    if completed:
        summary_parts.append(f"{completed} completed")
    if cancelled:
        summary_parts.append(f"{cancelled} cancelled")
    lines.append("\n" + ", ".join(summary_parts))

    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(validated),
        pending=pending,
        in_progress=in_progress,
    )


def read_todos(
    ctx: RunContext[CoDeps],
) -> ToolReturn:
    """Read the current session todo list.

    Call before ending a turn to verify completeness — if any items are
    still 'pending' or 'in_progress', continue working rather than responding
    as done.

    Returns a dict with:
    - display: formatted todo list — show directly to user
    - count: total items
    - pending: number of pending items
    - in_progress: number of in_progress items
    - todos: list of {content, status, priority} dicts

    Args: (none)
    """
    todos = ctx.deps.session.session_todos

    if not todos:
        return tool_output(
            "No active todo list for this session.",
            ctx=ctx,
            count=0,
            pending=0,
            in_progress=0,
            todos=[],
        )

    pending = sum(1 for t in todos if t["status"] == "pending")
    in_progress = sum(1 for t in todos if t["status"] == "in_progress")

    status_icon = {
        "pending": "○",
        "in_progress": "◐",
        "completed": "●",
        "cancelled": "✗",
    }
    lines = [f"Session todos ({len(todos)} items):\n"]
    for t in todos:
        icon = status_icon.get(t["status"], "?")
        priority_str = f" [{t['priority']}]" if t["priority"] != "medium" else ""
        lines.append(f"  {icon} {t['content']}{priority_str}  ({t['status']})")

    if pending or in_progress:
        lines.append(f"\n{pending} pending, {in_progress} in_progress — work is not complete.")
    else:
        lines.append("\nAll items completed or cancelled.")

    return tool_output(
        "\n".join(lines),
        ctx=ctx,
        count=len(todos),
        pending=pending,
        in_progress=in_progress,
        todos=list(todos),
    )
