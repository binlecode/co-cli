"""Behavioral tests for the dream daemon reviewer module.

Verifies: load_transcript respects max_message_count, SESSION_REVIEW exists in SourceTypeEnum.
No LLM, no network — filesystem only.
"""

from pathlib import Path

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart

from co_cli.memory.item import SourceTypeEnum
from co_cli.session.persistence import load_transcript


def _write_jsonl_session(path: Path, n: int) -> None:
    """Write n UserPromptPart messages to a JSONL session file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for i in range(n):
            msg = ModelRequest(parts=[UserPromptPart(content=f"message {i}")])
            line = ModelMessagesTypeAdapter.dump_json([msg])
            f.write(line.decode("utf-8") + "\n")


def test_load_transcript_respects_max_message_count(tmp_path: Path) -> None:
    """load_transcript with max_message_count=2 returns at most 2 messages from a 5-message file."""
    session_file = tmp_path / "sessions" / "abc.jsonl"
    _write_jsonl_session(session_file, 5)

    result = load_transcript(session_file, max_message_count=2)

    assert len(result) == 2


def test_load_transcript_without_limit_returns_all(tmp_path: Path) -> None:
    """load_transcript without max_message_count returns all messages."""
    session_file = tmp_path / "sessions" / "abc.jsonl"
    _write_jsonl_session(session_file, 5)

    result = load_transcript(session_file)

    assert len(result) == 5


def test_session_review_exists_in_source_type_enum() -> None:
    """SESSION_REVIEW must be a member of SourceTypeEnum."""
    assert SourceTypeEnum.SESSION_REVIEW == "session_review"
