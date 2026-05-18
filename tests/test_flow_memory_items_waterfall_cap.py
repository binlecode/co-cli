"""Tests for MemoryStore.search_memory_items() two-pass cap (user priority + waterfall)."""

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.index.store import IndexStore
from co_cli.memory.store import (
    _USER_PRIORITY_CAP,
    _WATERFALL_CHUNK_CAP,
    MemoryStore,
)

_KEYWORD = "waterfall_test_distinctive_keyword_abc"

_FTS5_CONFIG = SETTINGS.memory.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
        "chunk_tokens": 600,
        "chunk_overlap_tokens": 0,
    }
)
_STORE_CONFIG = SETTINGS.model_copy(update={"memory": _FTS5_CONFIG})


def _make_stores(tmp_path: Path) -> tuple[IndexStore, MemoryStore]:
    index = IndexStore(config=_STORE_CONFIG, db_path=tmp_path / "search.db")
    memory = MemoryStore(index=index, config=_STORE_CONFIG)
    return index, memory


def _write_memory_item(directory: Path, name: str, memory_kind: str, body: str) -> Path:
    """Write a minimal memory item .md file with valid YAML frontmatter."""
    content = (
        "---\n"
        f"id: {name}\n"
        f"created: 2025-01-01T00:00:00\n"
        f"memory_kind: {memory_kind}\n"
        f"title: {name}\n"
        "---\n"
        f"{body}\n"
    )
    path = directory / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_waterfall_count_cap(tmp_path: Path) -> None:
    """Waterfall pass stops at _WATERFALL_CHUNK_CAP even when more memory items match."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    short_body = f"{_KEYWORD} " + "x" * 90
    for i in range(_WATERFALL_CHUNK_CAP + 3):
        _write_memory_item(memory_dir, f"rule_{i:02d}", "rule", short_body)

    index, memory = _make_stores(tmp_path)
    try:
        memory.sync_dir(memory_dir)
        results = memory.search_memory_items(_KEYWORD, kinds=["rule"], limit=100)
        assert len(results) <= _WATERFALL_CHUNK_CAP, (
            f"expected at most {_WATERFALL_CHUNK_CAP} waterfall results, got {len(results)}"
        )
    finally:
        index.close()


def test_user_priority_cap(tmp_path: Path) -> None:
    """User priority pass stops at _USER_PRIORITY_CAP even when more user memory items match."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    short_body = f"{_KEYWORD} " + "x" * 90
    for i in range(_USER_PRIORITY_CAP + 3):
        _write_memory_item(memory_dir, f"user_{i:02d}", "user", short_body)

    index, memory = _make_stores(tmp_path)
    try:
        memory.sync_dir(memory_dir)
        results = memory.search_memory_items(_KEYWORD, kinds=["user"], limit=100)
        assert len(results) <= _USER_PRIORITY_CAP, (
            f"expected at most {_USER_PRIORITY_CAP} user-priority results, got {len(results)}"
        )
    finally:
        index.close()
