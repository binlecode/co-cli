"""Extract indexable messages from session JSONL transcripts.

Parses each JSONL line, skipping control markers (compact_boundary, session_meta)
and non-indexable parts (tool-call, tool-return, system-prompt, thinking, retry-prompt).
Returns only user-prompt and text (assistant) parts as ExtractedMessage records.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ExtractedMessage:
    """One indexable message part extracted from a session line."""

    line_index: int
    part_index: int
    role: str  # 'user' | 'assistant'
    content: str
    timestamp: str | None


def extract_messages(path: Path) -> list[ExtractedMessage]:
    """Extract user-prompt and assistant-text parts from a session JSONL file.

    Skips tool-call, tool-return, system-prompt, thinking, retry-prompt parts
    and compact-boundary / session-meta control lines.
    """
    results: list[ExtractedMessage] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_idx, raw_line in enumerate(f):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Malformed JSON at line %d in %s", line_idx, path.name)
                    continue
                _extract_from_line(data, line_idx, results)
    except OSError as exc:
        logger.warning("Cannot read session file %s: %s", path.name, exc)
    return results


def _extract_from_line(
    data: object,
    line_idx: int,
    results: list[ExtractedMessage],
) -> None:
    """Extract indexable parts from one parsed JSONL line, appending to results."""
    # Skip non-array lines: compact_boundary, session_meta
    if not isinstance(data, list) or not data:
        return
    msg = data[0]
    if not isinstance(msg, dict):
        return
    msg_timestamp = msg.get("timestamp")
    parts = msg.get("parts")
    if not isinstance(parts, list):
        return
    for part_idx, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        extracted = _extract_part(part, part_idx, line_idx, msg_timestamp)
        if extracted is not None:
            results.append(extracted)


def _extract_part(
    part: dict,
    part_idx: int,
    line_idx: int,
    msg_timestamp: object,
) -> ExtractedMessage | None:
    """Return an ExtractedMessage for a user-prompt or text part, or None to skip."""
    part_kind = part.get("part_kind")
    ts = _to_str(part.get("timestamp") or msg_timestamp)

    if part_kind == "user-prompt":
        content = part.get("content", "")
        # Multi-modal content is a list of typed sub-parts
        if isinstance(content, list):
            texts = [
                sub.get("text", "")
                for sub in content
                if isinstance(sub, dict) and sub.get("type") == "text"
            ]
            content = " ".join(texts)
        if not isinstance(content, str) or not content.strip():
            return None
        return ExtractedMessage(
            line_index=line_idx,
            part_index=part_idx,
            role="user",
            content=content.strip(),
            timestamp=ts,
        )

    if part_kind == "text":
        content = part.get("content", "")
        if not isinstance(content, str) or not content.strip():
            return None
        return ExtractedMessage(
            line_index=line_idx,
            part_index=part_idx,
            role="assistant",
            content=content.strip(),
            timestamp=ts,
        )

    return None


def _to_str(value: object) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)
