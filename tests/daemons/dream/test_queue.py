"""Behavioral tests for the dream daemon queue helpers.

Verifies: write/read round-trip, list_queue_files skips .tmp files,
move_to_done moves file, move_to_failed adds last_error and moves file.
No LLM, no network — filesystem only.
"""

from pathlib import Path

from co_cli.daemons.dream._queue import (
    list_queue_files,
    move_to_done,
    move_to_failed,
    read_queue_item,
    write_queue_item,
)


def test_write_read_round_trip(tmp_path: Path) -> None:
    """write_queue_item then read_queue_item returns the original payload."""
    queue_file = tmp_path / "item.json"
    payload = {"session_id": "abc123", "domain": "memory", "attempts": 0}

    write_queue_item(queue_file, payload)
    result = read_queue_item(queue_file)

    assert result == payload


def test_list_queue_files_skips_tmp_files(tmp_path: Path) -> None:
    """list_queue_files returns only .json files, never .tmp files."""
    (tmp_path / "a.json").write_text("{}")
    (tmp_path / "b.json").write_text("{}")
    (tmp_path / "c.tmp").write_text("{}")
    (tmp_path / "d.tmp").write_text("{}")

    result = list_queue_files(tmp_path)

    names = [p.name for p in result]
    assert "c.tmp" not in names
    assert "d.tmp" not in names
    assert "a.json" in names
    assert "b.json" in names


def test_list_queue_files_returns_sorted(tmp_path: Path) -> None:
    """list_queue_files returns .json files sorted lexicographically."""
    (tmp_path / "2024-01-03.json").write_text("{}")
    (tmp_path / "2024-01-01.json").write_text("{}")
    (tmp_path / "2024-01-02.json").write_text("{}")

    result = list_queue_files(tmp_path)

    names = [p.name for p in result]
    assert names == ["2024-01-01.json", "2024-01-02.json", "2024-01-03.json"]


def test_list_queue_files_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    """list_queue_files returns [] when the directory does not exist."""
    result = list_queue_files(tmp_path / "nonexistent")

    assert result == []


def test_move_to_done_moves_file(tmp_path: Path) -> None:
    """move_to_done moves the queue file into the done/ subdirectory."""
    queue_file = tmp_path / "item.json"
    queue_file.write_text('{"session_id": "x"}')
    done_dir = tmp_path / "done"

    move_to_done(queue_file, done_dir)

    assert not queue_file.exists()
    assert (done_dir / "item.json").exists()


def test_move_to_done_creates_done_dir(tmp_path: Path) -> None:
    """move_to_done creates the done/ directory if it does not exist."""
    queue_file = tmp_path / "item.json"
    queue_file.write_text("{}")
    done_dir = tmp_path / "done" / "nested"

    move_to_done(queue_file, done_dir)

    assert (done_dir / "item.json").exists()


def test_move_to_failed_adds_last_error_and_moves(tmp_path: Path) -> None:
    """move_to_failed writes last_error into the payload and moves the file to failed/."""
    queue_file = tmp_path / "item.json"
    payload = {"session_id": "abc", "domain": "memory", "attempts": 2}
    write_queue_item(queue_file, payload)
    failed_dir = tmp_path / "failed"

    move_to_failed(queue_file, failed_dir, "something went wrong")

    assert not queue_file.exists()
    failed_file = failed_dir / "item.json"
    assert failed_file.exists()
    result = read_queue_item(failed_file)
    assert result["last_error"] == "something went wrong"
    assert result["session_id"] == "abc"


def test_move_to_failed_creates_failed_dir(tmp_path: Path) -> None:
    """move_to_failed creates the failed/ directory if it does not exist."""
    queue_file = tmp_path / "item.json"
    queue_file.write_text("{}")
    failed_dir = tmp_path / "failed"
    assert not failed_dir.exists()

    move_to_failed(queue_file, failed_dir, "err")

    assert failed_dir.exists()
    assert (failed_dir / "item.json").exists()
