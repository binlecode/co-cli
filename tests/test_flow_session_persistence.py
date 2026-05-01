"""Behavioral tests for persist_session_history and load_transcript round-trip.

Production path: co_cli/memory/transcript.py
No LLM needed — filesystem only.

Each test guards a specific regression:
- test_normal_turn_appends_delta_to_existing_session: delta append miscounts
  cause messages to be written twice or skipped on reload.
- test_compaction_branches_to_child_session: compacted history lost (never
  branched) → session lost on resume after compaction.
- test_load_transcript_skips_pre_boundary_on_large_file: large session reloads
  full uncompacted history → OOM or wrong context on resume.
"""

from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from co_cli.memory.session import new_session_path
from co_cli.memory.transcript import (
    COMPACT_BOUNDARY_MARKER,
    SKIP_PRECOMPACT_THRESHOLD,
    append_messages,
    load_transcript,
    persist_session_history,
    write_compact_boundary,
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
        sessions_dir=sessions_dir,
        messages=first_pair,
        persisted_message_count=0,
        history_compacted=False,
    )
    assert returned_path == session_path

    # Second persist: append only the next 2 messages (delta from index 2).
    returned_path2 = persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
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
    first_req_content = next(
        p.content for p in loaded[0].parts if isinstance(p, UserPromptPart)
    )
    second_req_content = next(
        p.content for p in loaded[2].parts if isinstance(p, UserPromptPart)
    )
    assert first_req_content == "first user message"
    assert second_req_content == "second user message"


def test_compaction_branches_to_child_session(tmp_path: Path) -> None:
    """Compaction must branch to a fresh child session, leaving the parent intact.

    Failure mode: compacted history lost (never branched) → session context
    is missing on resume after compaction.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_path = new_session_path(sessions_dir)

    initial_msgs = [_req("pre-compaction user"), _resp("pre-compaction model")]
    append_messages(session_path, initial_msgs)

    compacted_msgs = [_req("compacted summary prompt"), _resp("compacted model reply")]

    child_path = persist_session_history(
        session_path=session_path,
        sessions_dir=sessions_dir,
        messages=compacted_msgs,
        persisted_message_count=2,
        history_compacted=True,
    )

    # Compaction must yield a different path, and the child file must exist.
    assert child_path != session_path, (
        "persist_session_history must branch to a new path when history_compacted=True"
    )
    assert child_path.exists(), "Child session file must be created"

    # Child contains only the compacted messages.
    child_loaded = load_transcript(child_path)
    assert len(child_loaded) == 2, (
        f"Child session must contain 2 compacted messages, got {len(child_loaded)}"
    )
    child_req_content = next(
        p.content for p in child_loaded[0].parts if isinstance(p, UserPromptPart)
    )
    assert child_req_content == "compacted summary prompt"

    # Parent is unchanged — original 2 messages still there, child content absent.
    parent_loaded = load_transcript(session_path)
    assert len(parent_loaded) == 2, (
        f"Parent session must still have 2 original messages, got {len(parent_loaded)}"
    )
    parent_req_content = next(
        p.content for p in parent_loaded[0].parts if isinstance(p, UserPromptPart)
    )
    assert parent_req_content == "pre-compaction user"

    # Verify compacted content does not bleed into parent.
    all_parent_contents = [
        p.content
        for msg in parent_loaded
        for p in (msg.parts if hasattr(msg, "parts") else [])
        if isinstance(p, (UserPromptPart, TextPart))
    ]
    assert "compacted summary prompt" not in all_parent_contents, (
        "Compacted message must not appear in the parent session"
    )


def test_load_transcript_skips_pre_boundary_on_large_file(tmp_path: Path) -> None:
    """Files above SKIP_PRECOMPACT_THRESHOLD must drop all messages before the boundary.

    Failure mode: large session reloads full uncompacted history → OOM or
    wrong context injected on resume.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    session_path = new_session_path(sessions_dir)

    pre_boundary_msgs = [
        _req("pre-boundary user A"),
        _resp("pre-boundary model A"),
    ]
    post_boundary_msgs = [
        _req("post-boundary user B"),
        _resp("post-boundary model B"),
    ]

    # Step 1: write pre-boundary messages.
    append_messages(session_path, pre_boundary_msgs)

    # Step 2: pad the file past SKIP_PRECOMPACT_THRESHOLD (5 MB) so the
    # load_transcript size-gate activates skip_precompact=True.
    padding_line = "# padding\n"
    total_padding = SKIP_PRECOMPACT_THRESHOLD + 1024  # just over the threshold
    repeats = total_padding // len(padding_line) + 1
    with session_path.open("a", encoding="utf-8") as f:
        f.write(padding_line * repeats)

    # Step 3: write the compact boundary marker.
    write_compact_boundary(session_path)

    # Step 4: write post-boundary messages.
    append_messages(session_path, post_boundary_msgs)

    # Sanity-check: file is indeed above the threshold.
    assert session_path.stat().st_size > SKIP_PRECOMPACT_THRESHOLD, (
        "Test setup error: file must exceed SKIP_PRECOMPACT_THRESHOLD before calling load_transcript"
    )

    loaded = load_transcript(session_path)

    assert len(loaded) == 2, (
        f"Expected 2 post-boundary messages, got {len(loaded)}. "
        "Pre-boundary messages must be dropped for large files."
    )

    loaded_contents = [
        p.content
        for msg in loaded
        for p in (msg.parts if hasattr(msg, "parts") else [])
        if isinstance(p, (UserPromptPart, TextPart))
    ]

    # Post-boundary content must be present.
    assert "post-boundary user B" in loaded_contents, (
        "Post-boundary UserPromptPart must survive the boundary skip"
    )
    assert "post-boundary model B" in loaded_contents, (
        "Post-boundary TextPart must survive the boundary skip"
    )

    # Pre-boundary content must be absent.
    assert "pre-boundary user A" not in loaded_contents, (
        "Pre-boundary UserPromptPart must be cleared by the compact boundary skip"
    )
    assert "pre-boundary model A" not in loaded_contents, (
        "Pre-boundary TextPart must be cleared by the compact boundary skip"
    )
