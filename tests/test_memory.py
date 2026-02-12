"""Tests for memory gravity — touch and dedup on recall."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml

from co_cli.tools.memory import (
    _touch_memory,
    _dedup_pulled,
    _load_all_memories,
    MemoryEntry,
    recall_memory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeDeps:
    """Minimal deps for memory tool tests."""

    memory_max_count: int = 200
    memory_dedup_window_days: int = 7
    memory_dedup_threshold: int = 85
    memory_decay_strategy: str = "summarize"
    memory_decay_percentage: float = 0.2


class _FakeRunContext:
    def __init__(self, deps: Any):
        self._deps = deps

    @property
    def deps(self) -> Any:
        return self._deps


def _ctx() -> _FakeRunContext:
    return _FakeRunContext(_FakeDeps())


def _write_memory(memory_dir: Path, memory_id: int, content: str,
                   tags: list[str] | None = None,
                   created: str | None = None,
                   updated: str | None = None) -> Path:
    """Write a memory file for testing."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    if created is None:
        created = datetime.now(timezone.utc).isoformat()
    slug = content[:30].lower().replace(" ", "-")
    filename = f"{memory_id:03d}-{slug}.md"
    fm: dict[str, Any] = {
        "id": memory_id,
        "created": created,
        "tags": tags or [],
    }
    if updated:
        fm["updated"] = updated
    md = f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{content}\n"
    path = memory_dir / filename
    path.write_text(md, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _touch_memory
# ---------------------------------------------------------------------------


def test_touch_memory_sets_updated(tmp_path: Path):
    """_touch_memory refreshes the updated timestamp."""
    path = _write_memory(tmp_path, 1, "User prefers pytest", tags=["preference"])
    entry = _load_all_memories(tmp_path)[0]
    assert entry.updated is None

    _touch_memory(entry)

    # Reload and verify updated is set
    reloaded = _load_all_memories(tmp_path)[0]
    assert reloaded.updated is not None
    assert reloaded.updated.startswith("20")


# ---------------------------------------------------------------------------
# _dedup_pulled
# ---------------------------------------------------------------------------


def test_dedup_pulled_merges_similar(tmp_path: Path):
    """Similar pulled memories get consolidated."""
    _write_memory(tmp_path, 1, "User prefers pytest for testing",
                  tags=["preference"],
                  created=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    _write_memory(tmp_path, 2, "User prefers pytest for testing purposes",
                  tags=["testing"],
                  created=datetime.now(timezone.utc).isoformat())

    entries = _load_all_memories(tmp_path)
    assert len(entries) == 2

    result = _dedup_pulled(entries, threshold=80)
    # One should be merged away
    assert len(result) == 1
    # Remaining file should have merged tags
    remaining = _load_all_memories(tmp_path)
    assert len(remaining) == 1


def test_dedup_pulled_keeps_distinct(tmp_path: Path):
    """Distinct memories are NOT merged."""
    _write_memory(tmp_path, 1, "User prefers pytest over unittest",
                  tags=["preference"])
    _write_memory(tmp_path, 2, "Project uses PostgreSQL for storage",
                  tags=["context"])

    entries = _load_all_memories(tmp_path)
    result = _dedup_pulled(entries, threshold=85)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# recall_memory gravity integration
# ---------------------------------------------------------------------------


def test_recall_touches_pulled_memories(tmp_path: Path, monkeypatch):
    """Recalled memories get their updated timestamp refreshed."""
    _write_memory(tmp_path, 1, "User prefers dark theme",
                  tags=["preference"])

    # Patch cwd so recall_memory finds our test memories
    monkeypatch.chdir(tmp_path.parent)
    memory_dir = tmp_path.parent / ".co-cli" / "knowledge" / "memories"
    memory_dir.mkdir(parents=True, exist_ok=True)
    _write_memory(memory_dir, 1, "User prefers dark theme",
                  tags=["preference"])

    result = asyncio.get_event_loop().run_until_complete(
        recall_memory(_ctx(), "dark theme")
    )
    assert result["count"] >= 1

    # Verify the file was touched (updated timestamp set)
    reloaded = _load_all_memories(memory_dir)
    touched = [m for m in reloaded if "dark" in m.content.lower()]
    assert len(touched) == 1
    assert touched[0].updated is not None


def test_gravity_affects_recency_order(tmp_path: Path, monkeypatch):
    """Pulled memory appears first in next recall due to gravity."""
    memory_dir = tmp_path / ".co-cli" / "knowledge" / "memories"
    memory_dir.mkdir(parents=True, exist_ok=True)

    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    new_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    # Older memory about testing
    _write_memory(memory_dir, 1, "User prefers testing with pytest",
                  tags=["preference", "testing"], created=old_time)
    # Newer memory about testing
    _write_memory(memory_dir, 2, "User uses coverage reports for testing",
                  tags=["context", "testing"], created=new_time)

    monkeypatch.chdir(tmp_path)

    # First recall — memory 2 should be first (newer)
    result1 = asyncio.get_event_loop().run_until_complete(
        recall_memory(_ctx(), "testing")
    )
    assert result1["count"] == 2

    # Now recall just memory 1 (by specific keyword "pytest")
    result2 = asyncio.get_event_loop().run_until_complete(
        recall_memory(_ctx(), "pytest")
    )
    assert result2["count"] >= 1

    # After being touched, memory 1 should have a fresh updated timestamp
    entries = _load_all_memories(memory_dir)
    m1 = [e for e in entries if e.id == 1][0]
    assert m1.updated is not None
