"""Behavioral test for compaction → session transcript rewrite-in-place.

Production path: when ``commit_compaction`` writes ``compaction_applied_this_turn=True``,
``_finalize_turn`` (co_cli/main.py) reads the flag and passes ``history_compacted=True``
to ``persist_session_history`` (co_cli/memory/transcript.py), which overwrites the
current transcript file in place rather than appending a delta.

This is the downstream consumer of the runtime field that ``commit_compaction`` writes.

Failure mode: pre-compaction history leaks into the reloaded transcript, or the
session path changes after compaction (forks a child file).

No LLM needed — filesystem only.
"""

from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from co_cli.memory.session import new_session_path
from co_cli.memory.transcript import (
    append_messages,
    load_transcript,
    persist_session_history,
)


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def test_compaction_rewrites_session_in_place(tmp_path: Path) -> None:
    """Compaction must overwrite the current transcript in place, not fork a child."""
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
