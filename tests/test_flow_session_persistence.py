"""Behavioral tests for persist_session_history and load_transcript round-trip.

Production path: co_cli/memory/transcript.py
No LLM needed — filesystem only.

Each test guards a specific regression:
- test_normal_turn_appends_delta_to_existing_session: delta append miscounts
  cause messages to be written twice or skipped on reload.
- test_compaction_rewrites_session_in_place: compaction must overwrite the
  current transcript in place — not fork a child file.
- test_load_transcript_rejects_oversized_file: files above MAX_TRANSCRIPT_READ_BYTES
  must be rejected to prevent OOM.
"""

from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from co_cli.memory.session import new_session_path
from co_cli.memory.transcript import (
    MAX_TRANSCRIPT_READ_BYTES,
    append_messages,
    load_transcript,
    persist_session_history,
)


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def test_normal_turn_appends_delta_to_existing_session(tmp_path: Path) -> None:
    """Delta append must produce exactly the union of all messages on reload.

    Failure mode: delta append miscounts → messages written twice or skipped on reload.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_path = new_session_path(sessions_dir)

    first_pair = [_req("first user message"), _resp("first model response")]
    second_pair = [_req("second user message"), _resp("second model response")]
    all_msgs = first_pair + second_pair

    # First persist: write the initial 2 messages (persisted_message_count=0).
    returned_path = persist_session_history(
        session_path=session_path,
        messages=first_pair,
        persisted_message_count=0,
        history_compacted=False,
    )
    assert returned_path == session_path

    # Second persist: append only the next 2 messages (delta from index 2).
    returned_path2 = persist_session_history(
        session_path=session_path,
        messages=all_msgs,
        persisted_message_count=2,
        history_compacted=False,
    )
    assert returned_path2 == session_path

    loaded = load_transcript(session_path)

    assert len(loaded) == 4, (
        f"Expected 4 messages after two persists, got {len(loaded)}. "
        "Delta may be double-written or skipped."
    )
    # Verify correct content order.
    assert isinstance(loaded[0], ModelRequest)
    assert isinstance(loaded[1], ModelResponse)
    assert isinstance(loaded[2], ModelRequest)
    assert isinstance(loaded[3], ModelResponse)
    first_req_content = next(p.content for p in loaded[0].parts if isinstance(p, UserPromptPart))
    second_req_content = next(p.content for p in loaded[2].parts if isinstance(p, UserPromptPart))
    assert first_req_content == "first user message"
    assert second_req_content == "second user message"


def test_compaction_rewrites_session_in_place(tmp_path: Path) -> None:
    """Compaction must overwrite the current transcript in place, not fork a child.

    Failure mode: pre-compaction history leaks into the reloaded transcript, or
    the session path changes after compaction.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_path = new_session_path(sessions_dir)

    initial_msgs = [_req("pre-compaction user"), _resp("pre-compaction model")]
    append_messages(session_path, initial_msgs)

    compacted_msgs = [_req("compacted summary prompt"), _resp("compacted model reply")]

    returned_path = persist_session_history(
        session_path=session_path,
        messages=compacted_msgs,
        persisted_message_count=2,
        history_compacted=True,
    )

    # In-place rewrite — same path returned.
    assert returned_path == session_path, (
        "persist_session_history must return the same path when history_compacted=True"
    )
    assert session_path.exists(), "Session file must still exist after in-place rewrite"

    # No sibling files created.
    session_files = list(sessions_dir.iterdir())
    assert len(session_files) == 1, (
        f"Expected exactly 1 session file after compaction, found {len(session_files)}"
    )

    # File contains only the compacted messages.
    loaded = load_transcript(session_path)
    assert len(loaded) == 2, (
        f"Expected 2 compacted messages after in-place rewrite, got {len(loaded)}"
    )
    loaded_req_content = next(p.content for p in loaded[0].parts if isinstance(p, UserPromptPart))
    assert loaded_req_content == "compacted summary prompt"

    # Pre-compaction content must not be present.
    all_contents = [
        p.content
        for msg in loaded
        for p in (msg.parts if hasattr(msg, "parts") else [])
        if isinstance(p, (UserPromptPart, TextPart))
    ]
    assert "pre-compaction user" not in all_contents, (
        "Pre-compaction message must be gone after in-place rewrite"
    )


def test_load_transcript_rejects_oversized_file(tmp_path: Path) -> None:
    """Files above MAX_TRANSCRIPT_READ_BYTES must be rejected to prevent OOM.

    Failure mode: load_transcript loads a giant file and exhausts memory.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_path = new_session_path(sessions_dir)

    # Write a couple of real messages first.
    append_messages(session_path, [_req("user message"), _resp("model response")])

    # Pad the file past MAX_TRANSCRIPT_READ_BYTES.
    padding_line = "# padding\n"
    total_padding = MAX_TRANSCRIPT_READ_BYTES + 1024
    repeats = total_padding // len(padding_line) + 1
    with session_path.open("a", encoding="utf-8") as f:
        f.write(padding_line * repeats)

    assert session_path.stat().st_size > MAX_TRANSCRIPT_READ_BYTES, (
        "Test setup error: file must exceed MAX_TRANSCRIPT_READ_BYTES"
    )

    loaded = load_transcript(session_path)

    assert loaded == [], f"Expected empty list for oversized file, got {len(loaded)} messages"
