"""JSONL transcript persistence for co-cli chat sessions.

Transcripts are stored as JSONL files at the session path (a Path object).
Each line is a single-element list serialized via ModelMessagesTypeAdapter,
containing one ModelMessage (request or response).

Transcript files are append-only — never rewritten, never truncated.
No TTL on transcripts — permanent until user deletes manually.

Compact boundary markers and session metadata are written as JSONL control
lines. On resume, messages before the last boundary are skipped for files
above the precompact threshold (5 MB), matching fork-claude-code's pattern.
"""

import json
import logging
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from co_cli.context.session import new_session_path

logger = logging.getLogger(__name__)

# Files below this threshold are loaded in full — no boundary scan.
# Above this, compact boundaries trigger pre-boundary skip on resume.
# Matches fork-claude-code's SKIP_PRECOMPACT_THRESHOLD.
SKIP_PRECOMPACT_THRESHOLD = 5 * 1024 * 1024  # 5 MB

# Read-side OOM guard — bail before loading files above this size.
# Matches fork-claude-code's MAX_TRANSCRIPT_READ_BYTES.
MAX_TRANSCRIPT_READ_BYTES = 50 * 1024 * 1024  # 50 MB

# Marker line written to JSONL when compaction replaces in-memory history.
# On resume, everything before the last occurrence is skipped (for large files).
COMPACT_BOUNDARY_MARKER = '{"type":"compact_boundary"}'
_SESSION_META_TYPE = "session_meta"


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


def write_compact_boundary(path: Path) -> None:
    """Write a compact boundary marker to the session transcript.

    On resume, load_transcript skips all messages before the last boundary
    (for files above SKIP_PRECOMPACT_THRESHOLD). This avoids loading the
    full uncompacted history — only post-compaction messages are returned.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(COMPACT_BOUNDARY_MARKER + "\n")
    path.chmod(0o600)


def write_session_meta(
    path: Path,
    *,
    parent_session_path: Path | None = None,
    reason: str | None = None,
) -> None:
    """Write a metadata control line for a newly branched session transcript."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": _SESSION_META_TYPE}
    if parent_session_path is not None:
        payload["parent_session"] = parent_session_path.name
    if reason:
        payload["reason"] = reason
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    path.chmod(0o600)


def persist_session_history(
    *,
    session_path: Path,
    sessions_dir: Path,
    messages: list[ModelMessage],
    persisted_message_count: int,
    history_compacted: bool,
    reason: str = "compaction",
) -> Path:
    """Persist history, branching to a child session when history was replaced.

    Normal turns append the positional tail after ``persisted_message_count``.
    When compaction replaced the in-memory transcript, a fresh child session is
    created, linked to the parent via a metadata control line, and the entire
    compacted history is written there. This preserves the full pre-compaction
    transcript while making the compacted continuation durable.
    """
    if history_compacted or len(messages) < persisted_message_count:
        new_path = new_session_path(sessions_dir)
        write_session_meta(new_path, parent_session_path=session_path, reason=reason)
        append_messages(new_path, messages)
        return new_path

    append_messages(session_path, messages[persisted_message_count:])
    return session_path


def load_transcript(path: Path) -> list[ModelMessage]:
    """Load a transcript from a session's JSONL file.

    For files above SKIP_PRECOMPACT_THRESHOLD (5 MB), messages before the
    last compact_boundary marker are skipped — only post-compaction messages
    are returned. Files above MAX_TRANSCRIPT_READ_BYTES (50 MB) are rejected
    entirely to prevent OOM.

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

    skip_precompact = file_size > SKIP_PRECOMPACT_THRESHOLD

    all_messages: list[ModelMessage] = []
    boundary_found = False

    try:
        with path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                if line == COMPACT_BOUNDARY_MARKER:
                    if skip_precompact:
                        all_messages.clear()
                        boundary_found = True
                    continue
                if line.startswith('{"type":"session_meta"'):
                    continue
                try:
                    parsed = ModelMessagesTypeAdapter.validate_json(line)
                    all_messages.extend(parsed)
                except Exception:
                    logger.warning(
                        "Skipping malformed line %d in %s",
                        line_num,
                        path.name,
                    )
    except OSError as e:
        logger.warning("Transcript read failed for session %s: %s", short_id, e)

    if skip_precompact and boundary_found:
        logger.info(
            "Transcript loaded with compact-boundary skip: %d post-boundary messages from %s",
            len(all_messages),
            path.name,
        )

    return all_messages
