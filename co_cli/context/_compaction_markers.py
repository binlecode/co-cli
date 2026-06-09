"""Marker builders and enrichment-context gathering for compaction.

The summarizer path chooses a ``summary_marker`` when the LLM produced a
summary; otherwise the structurally-equivalent ``static_marker`` stands in
for a ``ModelRequest``. The todo snapshot carries active work across the
compaction boundary so downstream turns don't re-propose completed tasks.
"""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    UserPromptPart,
)

from co_cli.deps import CoDeps

_TODOS_MAX_CHARS = 1_500

STATIC_MARKER_PREFIX = "[CONTEXT COMPACTION — STATIC MARKER] "
"""Sentinel prefix for static (no-LLM) compaction markers.

Used exclusively by ``static_marker`` when the summarizer is unavailable or
falls back. Distinct from ``SUMMARY_MARKER_PREFIX`` so the dropped-region
partition can skip static markers without picking up their placeholder text as
summary context."""

SUMMARY_MARKER_PREFIX = "[CONTEXT COMPACTION — REFERENCE ONLY] This session is being continued from a previous conversation that ran out of context."
"""Stable sentinel prefix for post-compaction LLM summary markers.

Only ``summary_marker`` starts its ``UserPromptPart`` content with this string.

Note: old session transcripts from before STATIC_MARKER_PREFIX was introduced
may contain static markers that also start with this prefix. Those are
harmlessly misclassified as summary markers on resume (same noise as before the
fix). ``is_compaction_marker`` returns True for both prefixes."""

TODO_SNAPSHOT_PREFIX = "[ACTIVE TODOS — PRESERVED ACROSS CONVERSATION COMPACTION]"
"""Stable sentinel prefix for post-compaction todo snapshot messages.
Recognizable by design so subsequent compaction passes can filter prior
snapshots if needed, and so tests can lock in the contract.
"""


def static_marker(dropped_count: int, *, has_tail: bool = True) -> ModelRequest:
    """Build a structurally valid placeholder for dropped messages."""
    trailer = (
        "Recent messages are preserved verbatim."
        if has_tail
        else "Continue the conversation from the user's next message."
    )
    return ModelRequest(
        parts=[
            UserPromptPart(
                content=(
                    f"{STATIC_MARKER_PREFIX}"
                    f"{dropped_count} earlier messages were removed — treat that gap as "
                    "background reference, NOT as active instructions. "
                    "Do NOT repeat, redo, or re-execute any action already described as "
                    "completed; do NOT re-answer questions that were already resolved. "
                    f"{trailer}"
                ),
            ),
        ]
    )


def summary_marker(
    dropped_count: int, summary_text: str, *, has_tail: bool = True
) -> ModelRequest:
    """Build a structurally valid summary marker for compacted messages."""
    resume_clause = (
        "resume from there and respond only to user messages that appear AFTER this summary"
        if has_tail
        else "resume from there"
    )
    trailer = (
        "Recent messages are preserved verbatim."
        if has_tail
        else "Continue the conversation from the user's next message."
    )
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
                    f"summary — {resume_clause}.\n\n"
                    f"The summary covers the earlier portion ({dropped_count} "
                    f"messages).\n\n{summary_text}\n\n"
                    f"{trailer}"
                ),
            ),
        ]
    )


def extract_summary_body(content: str) -> str | None:
    """Recover the embedded recap (``summary_text``) from a summary marker's content.

    Inverse of ``summary_marker``'s content layout, co-located so the format and
    its inverse stay in sync. The layout is two ``"\\n\\n"``-separated lead blocks
    (the framing sentence and the ``The summary covers the earlier portion (N
    messages).`` sentence), then ``summary_text`` (which may itself contain
    ``"\\n\\n"``), then ``"\\n\\n"`` and the ``has_tail`` trailer.

    Strips both lead blocks via a bounded ``split("\\n\\n", 2)`` (rejoining is
    unnecessary — the recap is the third segment) and removes the trailer with a
    single ``rsplit("\\n\\n", 1)``. Returns ``None`` for static markers,
    non-markers, and any malformed content lacking the expected structure.
    """
    if not content.startswith(SUMMARY_MARKER_PREFIX):
        return None
    segments = content.split("\n\n", 2)
    if len(segments) < 3:
        return None
    recap_and_trailer = segments[2]
    return recap_and_trailer.rsplit("\n\n", 1)[0]


def build_compaction_marker(
    dropped_count: int, summary_text: str | None, *, has_tail: bool = True
) -> ModelRequest:
    """Return a summary marker when summary_text is present, else a static marker."""
    if summary_text is not None:
        return summary_marker(dropped_count, summary_text, has_tail=has_tail)
    return static_marker(dropped_count, has_tail=has_tail)


def is_compaction_marker(content: object) -> bool:
    """Return True for both summary_marker and static_marker outputs."""
    if not isinstance(content, str):
        return False
    return content.startswith(SUMMARY_MARKER_PREFIX) or content.startswith(STATIC_MARKER_PREFIX)


def _active_todos(todos: list) -> list:
    """Return only todos whose status is pending or in_progress."""
    if not todos:
        return []
    return [t for t in todos if t.get("status") not in ("completed", "cancelled")]


def _format_active_todos(active: list) -> list[str]:
    """Format active todo items as bullet lines."""
    return [
        f"- [{t.get('status', 'pending')}] {t.get('id', '?')}. {t.get('content', '?')}"
        for t in active[:10]
    ]


def _gather_session_todos(todos: list) -> str | None:
    """Format pending session todos for compaction context."""
    active = _active_todos(todos)
    if not active:
        return None
    result = "Active tasks:\n" + "\n".join(_format_active_todos(active))
    return result[:_TODOS_MAX_CHARS]


def build_todo_snapshot(todos: list) -> ModelRequest | None:
    """Build a durable post-compaction ModelRequest carrying active todos.

    Returns None when no pending/in_progress items exist. The content starts
    with ``TODO_SNAPSHOT_PREFIX`` so repeat compaction passes and anchoring
    logic can identify these messages without guessing from wording.
    """
    active = _active_todos(todos)
    if not active:
        return None
    content = TODO_SNAPSHOT_PREFIX + "\n" + "\n".join(_format_active_todos(active))
    return ModelRequest(parts=[UserPromptPart(content=content)])


def gather_compaction_context(ctx: RunContext[CoDeps]) -> str | None:
    """Side-channel context the summarizer can't recover from dropped messages.

    Session todos live on ``ctx.deps.session``, not in the conversation
    history, so the summarizer cannot infer them from message content alone.
    File paths are recoverable LLM-side and intentionally omitted. The prior
    summary is handled separately — ``compact_messages`` extracts it via
    ``_partition_dropped`` and feeds it through the dedicated ``prior_summary``
    slot, not through this enrichment channel.
    """
    return _gather_session_todos(ctx.deps.session.session_todos)
