"""Flatten and chunk session JSONL transcripts for unified search indexing.

Pure functions — no DB I/O. Input is a list of ExtractedMessage records (from
indexer.py); output is a list of SessionChunk records ready for index_chunks().
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from co_cli.memory.indexer import ExtractedMessage, extract_messages

SESSION_CHUNK_TOKENS = 400
SESSION_CHUNK_OVERLAP = 80
SESSION_LINE_WRAP_CHARS = 800


@dataclass
class SessionChunk:
    """One indexable chunk of a session transcript."""

    text: str
    start_jsonl_line: int  # 1-indexed JSONL line number
    end_jsonl_line: int  # 1-indexed JSONL line number (inclusive)


def _wrap_content(content: str) -> list[str]:
    """Split content into slices of at most SESSION_LINE_WRAP_CHARS characters.

    Split priority: double-newline > single-newline > '. ' > word boundary > hard cut.
    """
    if len(content) <= SESSION_LINE_WRAP_CHARS:
        return [content]

    slices: list[str] = []
    remaining = content
    while remaining:
        if len(remaining) <= SESSION_LINE_WRAP_CHARS:
            slices.append(remaining)
            break

        candidate = remaining[:SESSION_LINE_WRAP_CHARS]
        split_at = -1

        for sep in ("\n\n", "\n", ". "):
            idx = candidate.rfind(sep)
            if idx > 0:
                split_at = idx + len(sep)
                break

        if split_at <= 0:
            idx = candidate.rfind(" ")
            if idx > 0:
                split_at = idx + 1

        if split_at <= 0:
            split_at = SESSION_LINE_WRAP_CHARS

        chunk = remaining[:split_at].rstrip()
        if chunk:
            slices.append(chunk)
        remaining = remaining[split_at:].lstrip()

    return [s for s in slices if s]


def flatten_session(
    messages: list[ExtractedMessage],
) -> tuple[list[str], list[int]]:
    """Render messages as role-prefixed lines with JSONL line-map.

    Prefix rules:
      role == 'user'        → 'User: <content>'
      role == 'assistant'   → 'Assistant: <content>'
      role == 'tool-call'   → 'Tool[<tool_name>](call)'
      role == 'tool-return' → 'Tool[<tool_name>](return): <content>'

    Sanitization (dropped before flattening):
      - assistant content len <= 10 with no immediately following tool-call → heartbeat
      - tool-return content len < 10 → empty/ack result

    Long content (> SESSION_LINE_WRAP_CHARS chars) wraps to multiple flat lines;
    each wrap slice gets the role prefix and the same line_map entry.

    Returns (flat_lines, line_map) where line_map[i] is the 1-indexed JSONL
    line number for flat_lines[i].
    """
    flat_lines: list[str] = []
    line_map: list[int] = []

    for i, msg in enumerate(messages):
        jsonl_1indexed = msg.line_index + 1

        if msg.role == "user":
            for slc in _wrap_content(msg.content):
                flat_lines.append(f"User: {slc}")
                line_map.append(jsonl_1indexed)

        elif msg.role == "assistant":
            is_heartbeat = len(msg.content) <= 10
            if is_heartbeat:
                next_is_tool_call = i + 1 < len(messages) and messages[i + 1].role == "tool-call"
                if not next_is_tool_call:
                    continue
            for slc in _wrap_content(msg.content):
                flat_lines.append(f"Assistant: {slc}")
                line_map.append(jsonl_1indexed)

        elif msg.role == "tool-call":
            tool_name = msg.tool_name or "unknown"
            flat_lines.append(f"Tool[{tool_name}](call)")
            line_map.append(jsonl_1indexed)

        elif msg.role == "tool-return":
            if len(msg.content) < 10:
                continue
            tool_name = msg.tool_name or "unknown"
            prefix = f"Tool[{tool_name}](return)"
            for slc in _wrap_content(msg.content):
                flat_lines.append(f"{prefix}: {slc}")
                line_map.append(jsonl_1indexed)

    return flat_lines, line_map


def chunk_flattened(
    flat_lines: list[str],
    line_map: list[int],
    *,
    chunk_tokens: int = SESSION_CHUNK_TOKENS,
    overlap_tokens: int = SESSION_CHUNK_OVERLAP,
) -> list[SessionChunk]:
    """Sliding-window token-uniform chunking over flat_lines.

    Token estimate: len(text) // 4 (matches co_cli/memory/chunker.py).
    Overlap: last N lines of the current chunk are prepended to the next.
    JSONL bounds: start = min(line_map[window]); end = max(line_map[window]).
    """
    if not flat_lines:
        return []

    chunk_chars = chunk_tokens * 4
    overlap_chars = overlap_tokens * 4

    chunks: list[SessionChunk] = []
    start = 0

    while start < len(flat_lines):
        acc = 0
        end = start

        while end < len(flat_lines):
            line_len = len(flat_lines[end])
            if acc + line_len > chunk_chars and end > start:
                # Snap forward to the next message boundary so no message is
                # split across chunks. Lines from the same message share a
                # line_map value; keep consuming until that value changes.
                while end < len(flat_lines) and line_map[end] == line_map[end - 1]:
                    acc += len(flat_lines[end]) + 1
                    end += 1
                break
            acc += line_len + 1  # +1 for joining newline
            end += 1

        if end == start:
            end = start + 1

        text = "\n".join(flat_lines[start:end])
        start_jsonl = min(line_map[start:end])
        end_jsonl = max(line_map[start:end])
        chunks.append(
            SessionChunk(text=text, start_jsonl_line=start_jsonl, end_jsonl_line=end_jsonl)
        )

        if end >= len(flat_lines):
            break

        # Overlap: back up from end to find lines fitting within overlap_chars
        overlap_acc = 0
        overlap_count = 0
        for k in range(end - 1, start - 1, -1):
            candidate = len(flat_lines[k]) + 1
            if overlap_acc + candidate > overlap_chars:
                break
            overlap_acc += candidate
            overlap_count += 1

        next_start = end - overlap_count
        if next_start <= start:
            next_start = start + 1

        # Snap forward to a message boundary so the next chunk doesn't start
        # mid-message. Prefer less overlap over starting inside a wrapped line.
        # next_start == end after the snap means zero overlap — safe, no infinite
        # loop because end > start is invariant at this point.
        while (
            next_start < end
            and next_start > 0
            and line_map[next_start] == line_map[next_start - 1]
        ):
            next_start += 1

        start = next_start

    return chunks


def chunk_session(
    jsonl_path: Path,
    *,
    chunk_tokens: int = SESSION_CHUNK_TOKENS,
    overlap_tokens: int = SESSION_CHUNK_OVERLAP,
) -> list[SessionChunk]:
    """High-level entry: extract_messages → flatten_session → chunk_flattened."""
    messages = extract_messages(jsonl_path)
    flat_lines, line_map = flatten_session(messages)
    return chunk_flattened(
        flat_lines, line_map, chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens
    )
