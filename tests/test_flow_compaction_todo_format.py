"""Behavioral tests for compaction todo format — id. prefix in snapshot lines.

Exercises: build_todo_snapshot emits the `- [{status}] {id}. {content}` format
required for snapshot-based resume rehydration. No LLM — pure function over todo lists.
"""

import re

from co_cli.context.compaction import (
    TODO_SNAPSHOT_PREFIX,
    build_todo_snapshot,
)
from co_cli.deps import TodoItem

_LINE_RE = re.compile(r"^- \[(\w+)\] ([^.\s]+)\. (.+)$")


def _make_item(
    id_: str, content: str, status: str = "pending", priority: str = "medium"
) -> TodoItem:
    return TodoItem(id=id_, content=content, status=status, priority=priority)


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
    assert content.startswith(TODO_SNAPSHOT_PREFIX)

    body_lines = [ln for ln in content.splitlines() if ln.startswith("- [")]
    assert len(body_lines) == 2  # cancelled item excluded
    for line in body_lines:
        assert _LINE_RE.match(line), f"Line does not match expected format: {line!r}"

    assert any("a1" in ln and "Alpha task" in ln for ln in body_lines)
    assert any("b2" in ln and "Beta task" in ln for ln in body_lines)
