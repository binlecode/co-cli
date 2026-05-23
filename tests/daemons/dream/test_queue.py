"""Behavioral tests for the dream daemon queue helpers.

Covers behavior NOT exercised by the integration drain path:
- list_queue_files skips .tmp files (atomic-write invariant — partial writes
  must not be picked up by the drain)
- list_queue_files returns sorted (FIFO order guarantees fair processing)
- move_to_failed injects last_error into the payload (diagnostic invariant
  not asserted anywhere else)

Round-trip / move-to-done / mkdir-parents behaviors are covered end-to-end
by tests/integration/test_daemon_crash_recovery.py.
"""

from pathlib import Path

from co_cli.daemons.dream._queue import (
    list_queue_files,
    move_to_failed,
    read_queue_item,
    write_queue_item,
)


def test_list_queue_files_skips_tmp_files(tmp_path: Path) -> None:
    """list_queue_files returns only .json files, never .tmp files.

    Atomic writes land as .tmp first then rename to .json. A drain that
    picked up .tmp files could read partial JSON.
    """
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
    """list_queue_files returns .json files sorted lexicographically (FIFO drain order)."""
    (tmp_path / "2024-01-03.json").write_text("{}")
    (tmp_path / "2024-01-01.json").write_text("{}")
    (tmp_path / "2024-01-02.json").write_text("{}")

    result = list_queue_files(tmp_path)

    names = [p.name for p in result]
    assert names == ["2024-01-01.json", "2024-01-02.json", "2024-01-03.json"]


def test_move_to_failed_adds_last_error_and_moves(tmp_path: Path) -> None:
    """move_to_failed writes last_error into the payload and moves the file to failed/.

    The last_error field is the diagnostic record for items that exhausted
    retries — no other code path injects it.
    """
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
