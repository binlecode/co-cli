"""Extract indexable messages from session JSONL transcripts.

Parses each JSONL line, skipping non-array lines and noise parts
(thinking, system-prompt, retry-prompt).
Returns user-prompt, text (assistant), tool-call, and tool-return parts as
ExtractedMessage records.
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
    # 'user' | 'assistant' | 'tool-call' | 'tool-return'
    role: str
    content: str
    timestamp: str | None
    tool_name: str | None = None


def extract_messages(path: Path) -> list[ExtractedMessage]:
    """Extract indexable parts from a session JSONL file."""
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
    """Return an ExtractedMessage for a retained part kind, or None to skip."""
    part_kind = part.get("part_kind")
    ts = _to_str(part.get("timestamp") or msg_timestamp)

    if part_kind == "user-prompt":
        content = part.get("content", "")
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

    if part_kind == "tool-call":
        tool_name = _to_str(part.get("tool_name"))
        if not tool_name:
            return None
        rendered_args = _render_tool_args(part.get("args"))
        content = f"{tool_name} {rendered_args}".strip()
        return ExtractedMessage(
            line_index=line_idx,
            part_index=part_idx,
            role="tool-call",
            content=content,
            timestamp=ts,
            tool_name=tool_name,
        )

    if part_kind == "tool-return":
        tool_name = _to_str(part.get("tool_name"))
        content = part.get("content", "")
        if not isinstance(content, str) or not content.strip():
            return None
        return ExtractedMessage(
            line_index=line_idx,
            part_index=part_idx,
            role="tool-return",
            content=content.strip(),
            timestamp=ts,
            tool_name=tool_name,
        )

    return None


def _render_tool_args(args: object) -> str:
    """Render tool-call arguments as readable text so their values are searchable.

    pydantic-ai serializes ToolCallPart.args as a JSON string; decode it to a
    compact JSON rendering (literal unicode) so leaf values match a recall query
    as decoded text — the searchable+citable content surface includes the
    arguments, not just the tool name. Non-JSON or empty args fall back to the
    raw string.
    """
    if args is None:
        return ""
    if isinstance(args, str):
        text = args.strip()
        if not text:
            return ""
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text
        return json.dumps(decoded, ensure_ascii=False)
    return json.dumps(args, ensure_ascii=False)


def _to_str(value: object) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)
