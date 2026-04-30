"""Tests for extract_messages — all four retained part kinds."""

from pathlib import Path

import pytest

from co_cli.memory.indexer import ExtractedMessage, extract_messages

FIXTURE = Path(__file__).parent / "fixtures" / "session_with_tool_turns.jsonl"


@pytest.fixture(scope="module")
def messages() -> list[ExtractedMessage]:
    assert FIXTURE.exists(), f"Fixture not found: {FIXTURE}"
    return extract_messages(FIXTURE)


def test_all_four_roles_present(messages: list[ExtractedMessage]) -> None:
    roles = {m.role for m in messages}
    assert roles >= {"user", "assistant", "tool-call", "tool-return"}


def test_tool_name_populated_on_tool_messages(messages: list[ExtractedMessage]) -> None:
    tool_msgs = [m for m in messages if m.role in ("tool-call", "tool-return")]
    assert tool_msgs, "Expected at least one tool-call or tool-return"
    for m in tool_msgs:
        assert m.tool_name is not None, f"Expected tool_name on {m.role} at line {m.line_index}"
        assert m.tool_name != "", (
            f"Expected non-empty tool_name on {m.role} at line {m.line_index}"
        )


def test_tool_name_none_on_non_tool_messages(messages: list[ExtractedMessage]) -> None:
    non_tool = [m for m in messages if m.role in ("user", "assistant")]
    assert non_tool, "Expected user/assistant messages"
    for m in non_tool:
        assert m.tool_name is None, (
            f"Expected no tool_name on {m.role} message at line {m.line_index}"
        )


def test_noise_parts_dropped(tmp_path: Path) -> None:
    """thinking, system-prompt, retry-prompt produce zero messages."""
    noise_jsonl = tmp_path / "noise.jsonl"
    import json

    noise_jsonl.write_text(
        json.dumps(
            [
                {
                    "kind": "response",
                    "parts": [
                        {"part_kind": "thinking", "content": "I am thinking..."},
                        {"part_kind": "system-prompt", "content": "You are helpful."},
                        {"part_kind": "retry-prompt", "content": "Retry this."},
                    ],
                }
            ]
        )
        + "\n"
    )
    result = extract_messages(noise_jsonl)
    assert result == [], f"Expected no messages from noise parts, got: {result}"


def test_control_lines_dropped(tmp_path: Path) -> None:
    """compact_boundary and session_meta dict lines produce zero messages."""
    import json

    control_jsonl = tmp_path / "control.jsonl"
    control_jsonl.write_text(
        json.dumps({"kind": "compact_boundary"})
        + "\n"
        + json.dumps({"kind": "session_meta", "session_id": "abc"})
        + "\n"
    )
    result = extract_messages(control_jsonl)
    assert result == []


def test_whitespace_only_content_dropped(tmp_path: Path) -> None:
    """Empty/whitespace-only content produces zero messages."""
    import json

    blank_jsonl = tmp_path / "blank.jsonl"
    blank_jsonl.write_text(
        json.dumps(
            [
                {
                    "kind": "request",
                    "parts": [{"part_kind": "user-prompt", "content": "   "}],
                }
            ]
        )
        + "\n"
        + json.dumps(
            [
                {
                    "kind": "response",
                    "parts": [{"part_kind": "text", "content": ""}],
                }
            ]
        )
        + "\n"
    )
    result = extract_messages(blank_jsonl)
    assert result == []


def test_line_index_is_zero_based(messages: list[ExtractedMessage]) -> None:
    line_indices = {m.line_index for m in messages}
    assert min(line_indices) == 0


def test_part_index_is_position_within_parts(tmp_path: Path) -> None:
    """part_index reflects position within the parts list."""
    import json

    multi_jsonl = tmp_path / "multi.jsonl"
    multi_jsonl.write_text(
        json.dumps(
            [
                {
                    "kind": "response",
                    "parts": [
                        {"part_kind": "thinking", "content": ".."},
                        {"part_kind": "text", "content": "hello world"},
                    ],
                }
            ]
        )
        + "\n"
    )
    result = extract_messages(multi_jsonl)
    assert len(result) == 1
    assert result[0].role == "assistant"
    assert result[0].part_index == 1
