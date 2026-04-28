"""Transcript window builder — formats message history for knowledge mining.

Provides ``_tag_messages`` and ``build_transcript_window``, used by the
dream-cycle miner (``co_cli/memory/dream.py``).
"""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def _tag_messages(messages: list) -> list[tuple[int, str, str]]:
    """Tag each extractable part in ``messages`` as ``(idx, kind, line)``.

    Walks ``ModelRequest`` and ``ModelResponse`` entries and flattens their
    parts into a tagged stream. ``idx`` preserves original turn order so
    callers can impose independent caps on ``text`` vs. ``tool`` entries and
    merge them back in order. Skips Read-tool output and oversized non-prose
    tool results.
    """
    tagged: list[tuple[int, str, str]] = []
    idx = 0

    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    text = part.content if isinstance(part.content, str) else str(part.content)
                    tagged.append((idx, "text", f"User: {text}"))
                    idx += 1
                elif isinstance(part, ToolReturnPart):
                    content = part.content if isinstance(part.content, str) else str(part.content)
                    if content.startswith("1→ "):
                        continue
                    if len(content) > 1000 and not any(
                        ch in content[:200] for ch in (".", "!", "?")
                    ):
                        continue
                    tagged.append(
                        (idx, "tool", f"Tool result ({part.tool_name}): {content[:300]}")
                    )
                    idx += 1
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    tagged.append((idx, "text", f"Co: {part.content}"))
                    idx += 1
                elif isinstance(part, ToolCallPart):
                    args_str = str(part.args)[:200]
                    tagged.append((idx, "tool", f"Tool({part.tool_name}): {args_str}"))
                    idx += 1

    return tagged


def build_transcript_window(messages: list, *, max_text: int = 10, max_tool: int = 10) -> str:
    """Extract conversation turns from a message list as plain text.

    Collects User/Co text lines and interleaved tool call/return lines.
    Caps at ``max_text`` text lines and ``max_tool`` tool lines, then merges
    back in original turn order.

    Args:
        messages: Message list (delta slice or full transcript).
        max_text: Maximum number of text lines to include (default 10).
        max_tool: Maximum number of tool lines to include (default 10).

    Returns:
        Formatted string of interleaved User/Co and tool lines.
    """
    tagged = _tag_messages(messages)

    text_entries = [(orig_idx, line) for orig_idx, kind, line in tagged if kind == "text"]
    tool_entries = [(orig_idx, line) for orig_idx, kind, line in tagged if kind == "tool"]

    merged = text_entries[-max_text:] + tool_entries[-max_tool:]
    merged.sort(key=lambda entry: entry[0])

    return "\n".join(line for _, line in merged)
