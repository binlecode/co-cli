"""Behavioral tests for session persistence — restore, transcript round-trip, oversized read.

Production paths:
  - co_cli/bootstrap/core.py:restore_session — picks the most recent session at startup.
  - co_cli/memory/transcript.py — append/load.

No LLM needed — filesystem only.

Each test guards a specific regression:
- test_restore_session_picks_most_recent: glob ordering bug → wrong session resumed.
- test_normal_turn_appends_delta_to_existing_session: delta append miscounts
  cause messages to be written twice or skipped on reload.
- test_load_transcript_rejects_oversized_file: files above MAX_TRANSCRIPT_READ_BYTES
  must be rejected to prevent OOM.

The compaction → in-place rewrite contract is covered separately in
test_flow_compaction_session_rewrite.py.
"""

from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from tests._settings import SETTINGS_NO_MCP

from co_cli.bootstrap.core import restore_session
from co_cli.commands.resume import _rehydrate_todos
from co_cli.context._compaction_markers import TODO_SNAPSHOT_PREFIX
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.display.core import TerminalFrontend
from co_cli.memory.session import new_session_path, session_filename
from co_cli.memory.transcript import (
    MAX_TRANSCRIPT_READ_BYTES,
    append_messages,
    load_transcript,
    persist_session_history,
)
from co_cli.tools.shell_backend import ShellBackend


def _req(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _resp(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def _make_deps(tmp_path: Path) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        memory_store=None,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        sessions_dir=tmp_path / "sessions",
        knowledge_dir=tmp_path / "knowledge",
    )


def test_restore_session_picks_most_recent(tmp_path: Path) -> None:
    """restore_session() must pick the most recent session by lexicographic filename sort.

    Failure mode: glob/sort order bug → user resumes the oldest session at startup,
    losing access to their most recent conversation.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    older = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC)
    old_path = sessions_dir / session_filename(older, "aaaaaaaa-0000-0000-0000-000000000000")
    new_path = sessions_dir / session_filename(newer, "bbbbbbbb-0000-0000-0000-000000000000")
    old_path.touch()
    new_path.touch()

    deps = _make_deps(tmp_path)
    result = restore_session(deps, TerminalFrontend())

    assert result == new_path, "restore_session() must pick the most recently dated session"


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


# ---------------------------------------------------------------------------
# 15. Resume rehydrates session_todos from the most recent todo_write ToolReturnPart
# ---------------------------------------------------------------------------


def test_rehydrate_todos_from_todo_write_tool_return(tmp_path: Path) -> None:
    """_rehydrate_todos restores session_todos from the most recent todo_write ToolReturnPart.

    Regression guard: if rehydration is skipped or reads the wrong message, todo_read
    after /resume returns empty and the model re-proposes completed work.
    """
    todos_state = [
        {"id": "t1", "content": "Research", "status": "completed", "priority": "high"},
        {"id": "t2", "content": "Write", "status": "in_progress", "priority": "medium"},
    ]
    messages = [
        _req("start a plan"),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="todo_write",
                    content="Todo list saved (2 items):",
                    metadata={"todos": todos_state, "count": 2, "pending": 0, "in_progress": 1},
                )
            ]
        ),
        _req("next turn"),
    ]

    result = _rehydrate_todos(messages)

    assert len(result) == 2
    assert result[0]["id"] == "t1"
    assert result[0]["status"] == "completed"
    assert result[1]["id"] == "t2"
    assert result[1]["status"] == "in_progress"


# ---------------------------------------------------------------------------
# 16. Resume with no todo_write calls → session_todos remains empty
# ---------------------------------------------------------------------------


def test_rehydrate_todos_returns_empty_when_no_todo_write_in_messages(tmp_path: Path) -> None:
    """_rehydrate_todos returns [] when messages contain no todo_write calls.

    Regression guard: if an empty return triggers None or an error, resume
    corrupts session state on sessions that never used todos.
    """
    messages = [
        _req("just a chat"),
        _resp("a plain response"),
        _req("another turn"),
    ]

    result = _rehydrate_todos(messages)

    assert result == []


# ---------------------------------------------------------------------------
# 17. Resume of compacted session — snapshot fallback, active items only, priority defaults
# ---------------------------------------------------------------------------


def test_rehydrate_todos_snapshot_fallback_returns_active_items_with_default_priority(
    tmp_path: Path,
) -> None:
    """/resume of a compacted session rehydrates active items from the TODO snapshot.

    The snapshot is the only source after compaction drops ToolReturnParts.
    build_todo_snapshot emits only pending/in_progress items (completed/cancelled
    are excluded at write time and are not recoverable). Priority defaults to 'medium'
    because the snapshot format omits it.

    Regression guard: if the fallback path is broken, users who resume a compacted
    session lose their in-flight plan silently.
    """
    # Snapshot body matches what build_todo_snapshot emits — active items only
    snapshot_body = (
        f"{TODO_SNAPSHOT_PREFIX}\n- [pending] s1. Step one\n- [in_progress] s2. Step two\n"
    )
    messages = [
        _req("first turn"),
        ModelRequest(parts=[UserPromptPart(content=snapshot_body)]),
        _req("next turn after compaction"),
    ]

    result = _rehydrate_todos(messages)

    ids = [t["id"] for t in result]
    assert "s1" in ids
    assert "s2" in ids
    for t in result:
        assert t["priority"] == "medium"
