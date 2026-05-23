"""Unit tests for _record_memory_recall: recall_count, last_recalled, recall_days updates.

No LLM. Real filesystem writes via real service layer.
Verifies that each recalled path gets its counters updated in the .md file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def _today_utc() -> str:
    """Return today's ISO date in UTC — matches _record_memory_recall's date logic."""
    return datetime.now(UTC).date().isoformat()


from tests._settings import SETTINGS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.item import load_memory_item
from co_cli.memory.service import save_memory_item
from co_cli.tools.memory.recall import _record_memory_recall
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(tmp_path: Path) -> CoDeps:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        memory_dir=memory_dir,
        index_store=None,
        memory_store=None,
    )


def _save_item(memory_dir: Path, title: str, content: str) -> Path:
    result = save_memory_item(memory_dir, content=content, memory_kind="note", title=title)
    return result.path


# ---------------------------------------------------------------------------
# recall_count
# ---------------------------------------------------------------------------


def test_recall_count_increments_by_one(tmp_path: Path) -> None:
    """_record_memory_recall increments recall_count by 1 for each item."""
    deps = _make_deps(tmp_path)
    path = _save_item(deps.memory_dir, "Item A", "Content A")

    item_before = load_memory_item(path)
    assert item_before.recall_count == 0

    _record_memory_recall(deps, [path])

    item_after = load_memory_item(path)
    assert item_after.recall_count == 1


def test_recall_count_accumulates_across_calls(tmp_path: Path) -> None:
    """Calling _record_memory_recall twice yields recall_count == 2."""
    deps = _make_deps(tmp_path)
    path = _save_item(deps.memory_dir, "Item B", "Content B")

    _record_memory_recall(deps, [path])
    _record_memory_recall(deps, [path])

    item = load_memory_item(path)
    assert item.recall_count == 2


# ---------------------------------------------------------------------------
# last_recalled
# ---------------------------------------------------------------------------


def test_last_recalled_is_set_after_recall(tmp_path: Path) -> None:
    """_record_memory_recall sets last_recalled to a non-None ISO string."""
    deps = _make_deps(tmp_path)
    path = _save_item(deps.memory_dir, "Item C", "Content C")

    item_before = load_memory_item(path)
    assert item_before.last_recalled_at is None

    _record_memory_recall(deps, [path])

    item_after = load_memory_item(path)
    assert item_after.last_recalled_at is not None
    # Should parse as a valid ISO datetime
    parsed = datetime.fromisoformat(item_after.last_recalled_at.replace("Z", "+00:00"))
    assert parsed is not None


def test_last_recalled_updates_on_second_call(tmp_path: Path) -> None:
    """last_recalled advances (or stays equal) across two consecutive recalls."""
    deps = _make_deps(tmp_path)
    path = _save_item(deps.memory_dir, "Item D", "Content D")

    _record_memory_recall(deps, [path])
    item_first = load_memory_item(path)
    ts_first = item_first.last_recalled_at

    _record_memory_recall(deps, [path])
    item_second = load_memory_item(path)
    ts_second = item_second.last_recalled_at

    assert ts_second is not None
    # second timestamp must be >= first (monotone or same second)
    assert ts_second >= ts_first


# ---------------------------------------------------------------------------
# recall_days
# ---------------------------------------------------------------------------


def test_recall_days_adds_today_iso_date(tmp_path: Path) -> None:
    """_record_memory_recall appends today's ISO date to recall_days."""
    deps = _make_deps(tmp_path)
    path = _save_item(deps.memory_dir, "Item E", "Content E")

    item_before = load_memory_item(path)
    assert item_before.recall_days == []

    _record_memory_recall(deps, [path])

    item_after = load_memory_item(path)
    today = _today_utc()
    assert today in item_after.recall_days


def test_recall_days_deduplicates_same_day(tmp_path: Path) -> None:
    """Calling _record_memory_recall twice on the same day does not duplicate the date."""
    deps = _make_deps(tmp_path)
    path = _save_item(deps.memory_dir, "Item F", "Content F")

    _record_memory_recall(deps, [path])
    _record_memory_recall(deps, [path])

    item = load_memory_item(path)
    today = _today_utc()
    assert item.recall_days.count(today) == 1


# ---------------------------------------------------------------------------
# Multiple items in one call
# ---------------------------------------------------------------------------


def test_multiple_items_all_updated(tmp_path: Path) -> None:
    """_record_memory_recall updates all paths passed in the list."""
    deps = _make_deps(tmp_path)
    path_a = _save_item(deps.memory_dir, "Multi A", "Content multi A")
    path_b = _save_item(deps.memory_dir, "Multi B", "Content multi B")

    _record_memory_recall(deps, [path_a, path_b])

    item_a = load_memory_item(path_a)
    item_b = load_memory_item(path_b)
    assert item_a.recall_count == 1
    assert item_b.recall_count == 1


# ---------------------------------------------------------------------------
# Missing file is silently skipped
# ---------------------------------------------------------------------------


def test_missing_path_is_skipped_without_raising(tmp_path: Path) -> None:
    """_record_memory_recall silently skips paths that do not exist."""
    deps = _make_deps(tmp_path)
    nonexistent = deps.memory_dir / "ghost-item.md"

    # Should not raise
    _record_memory_recall(deps, [nonexistent])
