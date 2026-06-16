"""JSONL transcript persistence for co-cli chat sessions.

Transcripts are stored as JSONL files at the session path (a Path object).
Each line is a single-element list serialized via ModelMessagesTypeAdapter,
containing one ModelMessage (request or response).

Normal turns append a delta tail. Compaction turns rewrite the file in place —
truncate and replace with the compacted message set. No child sessions, no
boundary markers, no pre-compaction history retained.
No TTL on transcripts — permanent until user deletes manually.
"""

import logging
import os
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

logger = logging.getLogger(__name__)

# 50 MB read-side OOM guard.
MAX_TRANSCRIPT_READ_BYTES = 50 * 1024 * 1024


def append_messages(path: Path, messages: list[ModelMessage]) -> None:
    """Append new ModelMessage entries as JSONL lines to the session transcript."""
    if not messages:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        ModelMessagesTypeAdapter.dump_json([msg]).decode("utf-8") + "\n" for msg in messages
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(payload)


def _write_messages(path: Path, messages: list[ModelMessage]) -> None:
    """Overwrite the transcript file with the given messages."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        ModelMessagesTypeAdapter.dump_json([msg]).decode("utf-8") + "\n" for msg in messages
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)


def persist_session_history(
    *,
    session_path: Path,
    messages: list[ModelMessage],
    persisted_message_count: int,
    history_compacted: bool,
) -> Path:
    """Persist the session transcript.

    Normal turns append the tail after persisted_message_count.
    Compaction turns rewrite the file in place with the compacted message set.
    """
    if history_compacted:
        _write_messages(session_path, messages)
    else:
        append_messages(session_path, messages[persisted_message_count:])
    return session_path


def load_transcript(
    path: Path,
    *,
    max_message_count: int | None = None,
) -> list[ModelMessage]:
    """Load a transcript from a session's JSONL file.

    When max_message_count is provided, return only the first N messages that
    parse successfully. Existing callers use the default None and are unaffected.
    """
    if not path.exists():
        return []

    short_id = path.stem[-8:]
    try:
        file_size = path.stat().st_size
    except OSError:
        return []

    if file_size > MAX_TRANSCRIPT_READ_BYTES:
        logger.warning(
            "Transcript too large to load (%d bytes, limit %d): %s",
            file_size,
            MAX_TRANSCRIPT_READ_BYTES,
            path.name,
        )
        return []

    messages: list[ModelMessage] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = ModelMessagesTypeAdapter.validate_json(line)
                    messages.extend(parsed)
                except Exception:
                    logger.warning(
                        "Skipping malformed line %d in %s",
                        line_num,
                        path.name,
                    )
                if max_message_count is not None and len(messages) >= max_message_count:
                    messages = messages[:max_message_count]
                    break
    except OSError as e:
        logger.warning("Transcript read failed for session %s: %s", short_id, e)

    return messages
