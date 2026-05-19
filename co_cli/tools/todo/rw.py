"""Session-scoped task tracking tools.

Provides a lightweight todo list the model uses to track sub-goals within a
session. State lives in CoDeps.session.session_todos — it is not persisted to disk and
is cleared when the session ends.

Pattern follows OpenCode (session/todo.ts) and Claude Code (TodoWrite tool):
todo_write replaces the entire list by default; merge=True updates by id without
touching the rest. todo_read surfaces current state for completeness verification.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.deps import CoDeps, TodoItem, VisibilityPolicyEnum
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output


class TodoItemInput(BaseModel):
    id: str
    content: str
    status: Literal["pending", "in_progress", "completed", "cancelled"]
    priority: Literal["high", "medium", "low"]


_VALID_STATUS = {"pending", "in_progress", "completed", "cancelled"}
_VALID_PRIORITY = {"high", "medium", "low"}

# id must not contain period or whitespace — keeps snapshot parser unambiguous
_INVALID_ID_RE = re.compile(r"[.\s]")


def _validate_fresh(item: dict, i: int, seen_ids: set[str]) -> tuple[TodoItem | None, list[str]]:
    """Full validation for a fresh-write item. Updates seen_ids in place."""
    errors: list[str] = []
    id_ = ""

    id_raw = item.get("id")
    raw_id_str = str(id_raw).strip() if id_raw is not None else ""
    if not raw_id_str:
        errors.append(f"Item {i}: missing or empty 'id'")
    elif _INVALID_ID_RE.search(raw_id_str):
        errors.append(f"Item {i}: 'id' must not contain '.' or whitespace — got '{raw_id_str}'")
    elif raw_id_str in seen_ids:
        errors.append(f"Item {i}: duplicate 'id' '{raw_id_str}' in payload")
    else:
        id_ = raw_id_str
        seen_ids.add(id_)

    content_raw = item.get("content")
    content = content_raw.strip() if isinstance(content_raw, str) else ""
    if not content:
        errors.append(f"Item {i}: missing or empty 'content'")

    status = item.get("status", "pending")
    if status not in _VALID_STATUS:
        errors.append(
            f"Item {i}: invalid status '{status}' — must be one of {sorted(_VALID_STATUS)}"
        )

    priority = item.get("priority", "medium")
    if priority not in _VALID_PRIORITY:
        errors.append(
            f"Item {i}: invalid priority '{priority}' — must be one of {sorted(_VALID_PRIORITY)}"
        )

    if errors:
        return None, errors
    return TodoItem(id=id_, content=content, status=status, priority=priority), []


def _validate_merge_update(item: dict, i: int) -> tuple[dict, list[str]]:
    """Partial validation for merge-update of an existing item.

    Caller must have already validated and extracted `id`. Only validates fields
    present in item; missing fields are left unchanged on the existing item.
    """
    errors: list[str] = []
    update: dict = {"id": item["id"]}

    if "content" in item:
        content_raw = item["content"]
        content = content_raw.strip() if isinstance(content_raw, str) else ""
        if not content:
            errors.append(f"Item {i}: 'content' must not be empty")
        else:
            update["content"] = content

    if "status" in item:
        status = item["status"]
        if status not in _VALID_STATUS:
            errors.append(
                f"Item {i}: invalid status '{status}' — must be one of {sorted(_VALID_STATUS)}"
            )
        else:
            update["status"] = status

    if "priority" in item:
        priority = item["priority"]
        if priority not in _VALID_PRIORITY:
            errors.append(
                f"Item {i}: invalid priority '{priority}' — must be one of {sorted(_VALID_PRIORITY)}"
            )
        else:
            update["priority"] = priority

    return update, errors


def _check_one_in_progress(items: list[TodoItem]) -> list[str]:
    """Return error list if more than one item is in_progress; empty list otherwise."""
    in_progress_ids = [t["id"] for t in items if t["status"] == "in_progress"]
    if len(in_progress_ids) > 1:
        return [
            f"Multiple items marked 'in_progress' — only ONE allowed at a time. "
            f"Offending ids: {', '.join(in_progress_ids)}. "
            f"Resolve by setting all but one to 'pending', 'completed', or 'cancelled'."
        ]
    return []


def _run_fresh(todos: list[dict[str, Any]]) -> tuple[list[TodoItem] | None, list[str]]:
    """Validate and build a full replacement list. Returns (validated, errors)."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    validated: list[TodoItem] = []

    for i, item in enumerate(todos):
        if not isinstance(item, dict):
            errors.append(f"Item {i}: expected dict, got {type(item).__name__}")
            continue
        new_item, errs = _validate_fresh(item, i, seen_ids)
        errors.extend(errs)
        if new_item:
            validated.append(new_item)

    if errors:
        return None, errors

    aggregate_errors = _check_one_in_progress(validated)
    if aggregate_errors:
        return None, aggregate_errors

    return validated, []


def _run_merge(
    todos: list[dict[str, Any]], existing: list[TodoItem]
) -> tuple[list[TodoItem] | None, list[str]]:
    """Validate payload and apply merge against existing. Returns (merged, errors)."""
    errors: list[str] = []
    existing_by_id = {t["id"]: t for t in existing}
    seen_ids_payload: set[str] = set()
    updates: dict[str, dict] = {}
    new_items: list[TodoItem] = []
    seen_new_ids: set[str] = set()

    for i, item in enumerate(todos):
        if not isinstance(item, dict):
            errors.append(f"Item {i}: expected dict, got {type(item).__name__}")
            continue
        id_raw = item.get("id")
        id_ = str(id_raw).strip() if id_raw is not None else ""
        if not id_:
            errors.append(f"Item {i}: missing or empty 'id'")
            continue
        if _INVALID_ID_RE.search(id_):
            errors.append(f"Item {i}: 'id' must not contain '.' or whitespace — got '{id_}'")
            continue
        if id_ in seen_ids_payload:
            errors.append(f"Item {i}: duplicate 'id' '{id_}' in payload")
            continue
        seen_ids_payload.add(id_)

        if id_ in existing_by_id:
            update, errs = _validate_merge_update(item, i)
            errors.extend(errs)
            if not errs:
                updates[id_] = update
        else:
            new_item, errs = _validate_fresh(item, i, seen_new_ids)
            errors.extend(errs)
            if new_item:
                new_items.append(new_item)

    if errors:
        return None, errors

    # mypy cannot prove {**TodoItem, **dict} satisfies TodoItem; spread is correct by construction
    merged: list[TodoItem] = [
        {**t, **updates[t["id"]]} if t["id"] in updates else t  # type: ignore[misc]
        for t in existing
    ]
    merged.extend(new_items)

    aggregate_errors = _check_one_in_progress(merged)
    if aggregate_errors:
        return None, aggregate_errors

    return merged, []


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_concurrent_safe=True)
def todo_write(
    ctx: RunContext[CoDeps],
    todos: list[TodoItemInput],
    merge: bool = False,
) -> ToolReturn:
    """Replace or merge-update the session todo list for tracking multi-step work.

    When to use: proactively for any directive requiring 3+ steps or non-trivial
    planning. Create the list before starting work, update status as each
    sub-goal completes, and only ONE item may be "in_progress" at a time — writes that produce more than one are rejected.

    When NOT to use: trivial single-step tasks where tracking adds no value.

    Model assigns and owns `id`; ids must be unique within the session and stable
    across merge calls. To rename an id, do a fresh write (merge=False).

    merge=False (default): replace the full list. Every item must have a valid id,
    non-empty content, and pass status/priority validation. On success,
    session_todos is replaced with the validated list.

    merge=True: update by id without touching the rest. Known ids update only the
    fields provided; unknown ids are appended as new items (full validation).
    Existing items not mentioned in the payload are preserved unchanged, in
    original order.

    Validation is all-or-nothing for both modes: if any item fails, nothing is
    saved and session_todos is unchanged.

    Each item must have:
    - id (str): model-assigned; unique within session; no '.' or whitespace
    - content (str): task description — must be non-empty
    - status (str): "pending" | "in_progress" | "completed" | "cancelled"
    - priority (str): "high" | "medium" | "low"

    Returns a dict with:
    - display: summary of the todo list after the operation — show directly to user
    - count: number of items in the list after the operation
    - pending: number of pending items
    - in_progress: number of in_progress items
    - todos: full post-state list of {id, content, status, priority} dicts

    Args:
        todos: Items to write or merge. Fresh mode replaces the list. Merge mode updates by id.
        merge: If True, merge-update by id instead of replacing the full list.
    """
    todos_dicts = [t.model_dump() for t in todos]
    if merge:
        final, errors = _run_merge(todos_dicts, ctx.deps.session.session_todos)
        reject_msg = "Todo list NOT updated — validation errors:\n"
    else:
        final, errors = _run_fresh(todos_dicts)
        reject_msg = "Todo list NOT saved — validation errors:\n"

    if errors:
        return tool_output(
            reject_msg + "\n".join(f"  - {e}" for e in errors),
            ctx=ctx,
            count=0,
            pending=0,
            in_progress=0,
            errors=errors,
        )

    assert final is not None
    ctx.deps.session.session_todos = final

    pending = sum(1 for t in final if t["status"] == "pending")
    in_progress = sum(1 for t in final if t["status"] == "in_progress")
    completed = sum(1 for t in final if t["status"] == "completed")
    cancelled = sum(1 for t in final if t["status"] == "cancelled")

    action = "updated" if merge else "saved"
    lines = [f"Todo list {action} ({len(final)} items):\n"]
    status_icon = {"pending": "○", "in_progress": "◐", "completed": "●", "cancelled": "✗"}
    for t in final:
        icon = status_icon.get(t["status"], "?")
        priority_str = f" [{t['priority']}]" if t["priority"] != "medium" else ""
        lines.append(f"  {icon} {t['id']}. {t['content']}{priority_str}  ({t['status']})")

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
        count=len(final),
        pending=pending,
        in_progress=in_progress,
        todos=list(ctx.deps.session.session_todos),
    )


@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
def todo_read(
    ctx: RunContext[CoDeps],
) -> ToolReturn:
    """Read the current session todo list to verify progress and completeness.

    When to use: before ending a turn — if any items are still "pending" or
    "in_progress", continue working rather than responding as done. Also useful
    mid-task to check what remains.

    When NOT to use: when no todo list has been created for this session.

    Returns a dict with:
    - display: formatted todo list — show directly to user
    - count: total items
    - pending: number of pending items
    - in_progress: number of in_progress items
    - todos: list of {id, content, status, priority} dicts

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

    status_icon = {"pending": "○", "in_progress": "◐", "completed": "●", "cancelled": "✗"}
    lines = [f"Session todos ({len(todos)} items):\n"]
    for t in todos:
        icon = status_icon.get(t["status"], "?")
        priority_str = f" [{t['priority']}]" if t["priority"] != "medium" else ""
        lines.append(f"  {icon} {t['id']}. {t['content']}{priority_str}  ({t['status']})")

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
