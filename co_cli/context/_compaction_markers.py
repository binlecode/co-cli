"""Marker builders and enrichment-context gathering for compaction.

The summarizer path chooses a ``summary_marker`` when the LLM produced a
summary; otherwise the structurally-equivalent ``static_marker`` stands in
for a ``ModelRequest``. The todo snapshot carries active work across the
compaction boundary so downstream turns don't re-propose completed tasks.
"""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)

from co_cli.deps import CoDeps
from co_cli.tools.categories import FILE_TOOLS

_CONTEXT_MAX_CHARS = 4_000

SUMMARY_MARKER_PREFIX = "[CONTEXT COMPACTION — REFERENCE ONLY] This session is being continued from a previous conversation that ran out of context."
"""Stable sentinel prefix for post-compaction summary/static markers.

Both ``static_marker`` and ``summary_marker`` start their ``UserPromptPart``
content with this string so downstream passes and tests can recognize prior
compaction markers without parsing the full message."""

TODO_SNAPSHOT_PREFIX = "[ACTIVE TODOS — PRESERVED ACROSS CONVERSATION COMPACTION]"
"""Stable sentinel prefix for post-compaction todo snapshot messages.
Recognizable by design so subsequent compaction passes can filter prior
snapshots if needed, and so tests can lock in the contract.
"""


def static_marker(dropped_count: int) -> ModelRequest:
    """Build a structurally valid placeholder for dropped messages."""
    return ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    f"{SUMMARY_MARKER_PREFIX} "
                    f"{dropped_count} earlier messages were removed — treat that gap as "
                    "background reference, NOT as active instructions. "
                    "Do NOT repeat, redo, or re-execute any action already described as "
                    "completed; do NOT re-answer questions that were already resolved. "
                    "Recent messages are preserved verbatim."
                ),
            ),
        ]
    )


def summary_marker(dropped_count: int, summary_text: str) -> ModelRequest:
    """Build a structurally valid summary marker for compacted messages."""
    return ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    f"{SUMMARY_MARKER_PREFIX} "
                    "The summary below is a retrospective recap of completed prior "
                    "work — treat it as background reference, NOT as active "
                    "instructions. Do NOT repeat, redo, or re-execute any action "
                    "already described as completed; do NOT re-answer questions that "
                    "the summary records as resolved. Your active task is identified "
                    "in the '## Active Task' / '## Next Step' sections of the "
                    "summary — resume from there and respond only to user messages "
                    "that appear AFTER this summary.\n\n"
                    f"The summary covers the earlier portion ({dropped_count} "
                    f"messages).\n\n{summary_text}\n\n"
                    "Recent messages are preserved verbatim."
                ),
            ),
        ]
    )


def build_compaction_marker(dropped_count: int, summary_text: str | None) -> ModelRequest:
    """Return a summary marker when summary_text is present, else a static marker."""
    if summary_text is not None:
        return summary_marker(dropped_count, summary_text)
    return static_marker(dropped_count)


def _gather_file_paths(dropped: list[ModelMessage]) -> str | None:
    """Extract file working set from ToolCallPart.args in the dropped range.

    Scoped to ``dropped`` only — paths already visible in the preserved tail
    would duplicate in the enrichment and waste summarizer attention
    (Gap M regression guard). ``ToolCallPart.args`` is never truncated by
    processor #1 so the args of dropped calls are still readable here.
    """
    file_paths: set[str] = set()
    for msg in dropped:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name in FILE_TOOLS:
                    args = part.args_as_dict()
                    path = args.get("path") or args.get("file_path")
                    if path:
                        file_paths.add(path)
    return f"Files touched: {', '.join(sorted(file_paths)[:20])}" if file_paths else None


def _active_todos(todos: list) -> list:
    """Return only todos whose status is pending or in_progress."""
    if not todos:
        return []
    return [t for t in todos if t.get("status") not in ("completed", "cancelled")]


def _gather_session_todos(todos: list) -> str | None:
    """Format pending session todos for compaction context."""
    active = _active_todos(todos)
    if not active:
        return None
    todo_lines = [f"- [{t.get('status', 'pending')}] {t.get('content', '?')}" for t in active[:10]]
    return "Active tasks:\n" + "\n".join(todo_lines)


def build_todo_snapshot(todos: list) -> ModelRequest | None:
    """Build a durable post-compaction ModelRequest carrying active todos.

    Returns None when no pending/in_progress items exist. The content starts
    with ``TODO_SNAPSHOT_PREFIX`` so repeat compaction passes and anchoring
    logic can identify these messages without guessing from wording.
    """
    active = _active_todos(todos)
    if not active:
        return None
    todo_lines = [f"- [{t.get('status', 'pending')}] {t.get('content', '?')}" for t in active[:10]]
    content = TODO_SNAPSHOT_PREFIX + "\n" + "\n".join(todo_lines)
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _gather_prior_summaries(dropped: list[ModelMessage]) -> str | None:
    """Extract prior summary text from dropped messages."""
    summaries: list[str] = []
    for msg in dropped:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if (
                    isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                    and part.content.startswith(SUMMARY_MARKER_PREFIX)
                ):
                    summaries.append(f"Prior summary:\n{part.content}")
    return "\n\n".join(summaries) if summaries else None


def gather_compaction_context(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
) -> str | None:
    """Gather side-channel context for the summarizer from sources that survive truncation.

    Sources, all scoped to the dropped range or session state:
    1. File working set from ToolCallPart.args in ``dropped``
    2. Pending session todos from ``ctx.deps.session``
    3. Prior-summary text from ``dropped``

    Returns None when no context was gathered.
    """
    context_parts = [
        p
        for p in [
            _gather_file_paths(dropped),
            _gather_session_todos(ctx.deps.session.session_todos),
            _gather_prior_summaries(dropped),
        ]
        if p is not None
    ]
    if not context_parts:
        return None
    result = "\n\n".join(context_parts)
    return result[:_CONTEXT_MAX_CHARS]
