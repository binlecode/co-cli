"""Slash command handler for /resume."""

from __future__ import annotations

import re

from co_cli.commands.types import CommandContext, ReplaceTranscript
from co_cli.context.compaction import TODO_SNAPSHOT_PREFIX
from co_cli.deps import TodoItem
from co_cli.display.core import console

# id charset rule: no period or whitespace — mirrors _INVALID_ID_RE in rw.py
_SNAPSHOT_VALID_STATUS = {"pending", "in_progress", "completed", "cancelled"}
_SNAPSHOT_LINE_RE = re.compile(r"^- \[(\w+)\] ([^.\s]+)\. (.+)$")


def _parse_snapshot_lines(snapshot: str) -> list[TodoItem]:
    """Parse a TODO_SNAPSHOT_PREFIX message body into TodoItem dicts.

    Each active line has the shape `- [{status}] {id}. {content}`.
    Lines that don't match, have invalid status, or are missing id are skipped.
    Priority is not in the snapshot; defaults to 'medium'.
    """
    items: list[TodoItem] = []
    for line in snapshot.splitlines()[1:]:  # skip the prefix line
        m = _SNAPSHOT_LINE_RE.match(line)
        if not m:
            continue
        status, id_, content = m.group(1), m.group(2).strip(), m.group(3).strip()
        if not id_ or status not in _SNAPSHOT_VALID_STATUS:
            continue
        items.append(TodoItem(id=id_, content=content, status=status, priority="medium"))
    return items


def _rehydrate_todos(messages: list) -> list[TodoItem]:
    """Find the most recent todo state in loaded messages.

    Primary: most recent todo_write ToolReturnPart with metadata['todos'].
    Fallback: most recent TODO_SNAPSHOT_PREFIX UserPromptPart (compacted sessions).
    Returns [] if neither is found.

    Defensive: drops any item without a non-empty id (guards against pre-delivery
    transcripts that predate the id field).
    """
    from pydantic_ai.messages import ToolReturnPart, UserPromptPart

    for msg in reversed(messages):
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolReturnPart) and part.tool_name == "todo_write":
                meta = getattr(part, "metadata", None)
                todos = meta.get("todos") if isinstance(meta, dict) else None
                if isinstance(todos, list):
                    return [
                        t for t in todos if isinstance(t, dict) and str(t.get("id", "")).strip()
                    ]
            if isinstance(part, UserPromptPart):
                content = getattr(part, "content", "")
                if isinstance(content, str) and content.startswith(TODO_SNAPSHOT_PREFIX):
                    return _parse_snapshot_lines(content)
    return []


async def _cmd_resume(ctx: CommandContext, args: str) -> ReplaceTranscript | None:
    """Resume a past session via interactive picker."""
    from co_cli.display.core import prompt_selection
    from co_cli.session.browser import format_file_size, list_sessions
    from co_cli.session.persistence import load_transcript

    sessions = list_sessions(ctx.deps.sessions_dir)
    if not sessions:
        console.print("[dim]No past sessions found.[/dim]")
        return None

    items: list[str] = []
    for s in sessions:
        date_str = s.last_modified.strftime("%Y-%m-%d %H:%M")
        items.append(f"{s.title} ({date_str} · {format_file_size(s.file_size)})")

    selection = prompt_selection(items, title="Resume session")
    if selection is None:
        return None

    selected_idx = items.index(selection)
    selected = sessions[selected_idx]

    messages = load_transcript(selected.path)
    if not messages:
        console.print("[dim]Could not load transcript (empty or too large).[/dim]")
        return None
    ctx.deps.session.session_path = selected.path
    ctx.deps.session.session_todos = _rehydrate_todos(messages)
    return ReplaceTranscript(history=messages)
