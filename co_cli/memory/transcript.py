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
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

logger = logging.getLogger(__name__)

# Read-side OOM guard — bail before loading files above this size.
# 50 MB
MAX_TRANSCRIPT_READ_BYTES = 50 * 1024 * 1024


def append_messages(path: Path, messages: list[ModelMessage]) -> None:
    """Append new ModelMessage entries as JSONL lines to the session transcript.

    Each message is serialized as a single-element list via ModelMessagesTypeAdapter.
    Creates the file (and parent directories) on first call.
    """
    if not messages:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for msg in messages:
            line = ModelMessagesTypeAdapter.dump_json([msg])
            f.write(line.decode("utf-8") + "\n")
    path.chmod(0o600)


def _write_messages(path: Path, messages: list[ModelMessage]) -> None:
    """Overwrite the transcript file with the given messages (truncate + write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for msg in messages:
            line = ModelMessagesTypeAdapter.dump_json([msg])
            f.write(line.decode("utf-8") + "\n")
    path.chmod(0o600)


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
    Always returns session_path unchanged.
    """
    if history_compacted:
        _write_messages(session_path, messages)
    else:
        append_messages(session_path, messages[persisted_message_count:])
    return session_path


def load_transcript(path: Path) -> list[ModelMessage]:
    """Load a transcript from a session's JSONL file.

    Rejects files above MAX_TRANSCRIPT_READ_BYTES (50 MB) to prevent OOM.
    Returns the deserialized list of ModelMessage objects.
    Skips malformed lines with a warning.
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
    except OSError as e:
        logger.warning("Transcript read failed for session %s: %s", short_id, e)

    return messages
