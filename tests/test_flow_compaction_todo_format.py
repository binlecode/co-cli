"""Behavioral tests for compaction todo format — id. prefix in snapshot lines.

Exercises: _gather_session_todos and build_todo_snapshot both emit the
`- [{status}] {id}. {content}` format required for snapshot-based resume
rehydration. No LLM — pure function over todo lists.
"""

import re

from co_cli.context._compaction_markers import (
    TODO_SNAPSHOT_PREFIX,
    _gather_session_todos,
    build_todo_snapshot,
)
from co_cli.deps import TodoItem

_LINE_RE = re.compile(r"^- \[(\w+)\] ([^.\s]+)\. (.+)$")


def _make_item(
    id_: str, content: str, status: str = "pending", priority: str = "medium"
) -> TodoItem:
    return TodoItem(id=id_, content=content, status=status, priority=priority)


# ---------------------------------------------------------------------------
# 13. _gather_session_todos — active lines match - [{status}] {id}. {content}
# ---------------------------------------------------------------------------


def test_gather_session_todos_lines_match_id_prefix_format() -> None:
    """_gather_session_todos output has each active line in - [{status}] {id}. {content} format.

    Regression guard: if the format changes, the snapshot-based resume fallback
    (_parse_snapshot_lines) cannot parse the id and silently drops all items.
    """
    todos = [
        _make_item("task-1", "Write tests", "pending"),
        _make_item("task-2", "Run lint", "in_progress"),
        _make_item("task-3", "Deploy", "completed"),  # excluded: terminal
    ]

    result = _gather_session_todos(todos)

    assert result is not None
    lines = result.splitlines()
    # Skip the "Active tasks:" header line
    body_lines = [ln for ln in lines if ln.startswith("- [")]
    assert len(body_lines) == 2  # completed item excluded
    for line in body_lines:
        assert _LINE_RE.match(line), f"Line does not match expected format: {line!r}"

    assert any("task-1" in ln and "Write tests" in ln for ln in body_lines)
    assert any("task-2" in ln and "Run lint" in ln for ln in body_lines)


# ---------------------------------------------------------------------------
# 14. build_todo_snapshot — body lines match - [{status}] {id}. {content}
# ---------------------------------------------------------------------------


def test_build_todo_snapshot_body_lines_match_id_prefix_format() -> None:
    """build_todo_snapshot content has each active line in - [{status}] {id}. {content} format.

    Regression guard: the snapshot is the compaction-fallback source for resume
    rehydration. Wrong format → ids not parsed → session_todos silently empty after /resume.
    """
    todos = [
        _make_item("a1", "Alpha task", "pending"),
        _make_item("b2", "Beta task", "in_progress"),
        _make_item("c3", "Done task", "cancelled"),  # excluded: terminal
    ]

    snapshot = build_todo_snapshot(todos)

    assert snapshot is not None
    assert len(snapshot.parts) == 1
    content = snapshot.parts[0].content  # type: ignore[union-attr]
    assert isinstance(content, str)
    assert content.startswith(TODO_SNAPSHOT_PREFIX)

    body_lines = [ln for ln in content.splitlines() if ln.startswith("- [")]
    assert len(body_lines) == 2  # cancelled item excluded
    for line in body_lines:
        assert _LINE_RE.match(line), f"Line does not match expected format: {line!r}"

    assert any("a1" in ln and "Alpha task" in ln for ln in body_lines)
    assert any("b2" in ln and "Beta task" in ln for ln in body_lines)
