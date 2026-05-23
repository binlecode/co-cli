"""Tests for IndexStore + MemoryStore — chunked FTS5 search and canon indexing."""

from pathlib import Path

import yaml
from tests._settings import SETTINGS, make_settings

from co_cli.index.chunk import Chunk
from co_cli.index.store import IndexStore
from co_cli.memory.store import MEMORY_SOURCE, MemoryStore

_CANON_BODY = """\
TARS humor is tactical: front-loaded, delivered flat, never announced.
The setup arrives before the weight. Reverse the sequence and sincerity becomes relief.
"""

_FTS5_CONFIG = SETTINGS.memory.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_STORE_CONFIG = SETTINGS.model_copy(update={"memory": _FTS5_CONFIG})


def _write_canon_file(path: Path, body: str) -> None:
    frontmatter = "---\nauto_category: character\nread_only: true\n---\n\n"
    path.write_text(frontmatter + body, encoding="utf-8")


def _make_index(tmp_path: Path) -> IndexStore:
    return IndexStore(config=_STORE_CONFIG, db_path=tmp_path / "search.db")


def _index_canon_file(index: IndexStore, path: Path, body: str) -> None:
    """Index a canon file with no chunking (one chunk per file) — replicates bootstrap path."""
    import hashlib

    body_stripped = body.strip()
    chunk = Chunk(
        index=0,
        content=body_stripped,
        start_line=0,
        end_line=max(0, len(body_stripped.splitlines()) - 1),
    )
    with index.transaction() as tx:
        tx.upsert(
            source="canon",
            kind="canon",
            path=str(path),
            title=path.stem,
            mtime=path.stat().st_mtime,
            hash=hashlib.sha256(path.read_text().encode()).hexdigest(),
        )
        tx.index_chunks("canon", str(path), [chunk])


def test_get_chunk_content_returns_full_body(tmp_path: Path) -> None:
    """get_chunk_content() returns the complete post-frontmatter body."""
    canon_dir = tmp_path / "canon"
    canon_dir.mkdir()
    file_path = canon_dir / "scene-a.md"
    _write_canon_file(file_path, _CANON_BODY)

    index = _make_index(tmp_path)
    try:
        _index_canon_file(index, file_path, _CANON_BODY)
        content = index.get_chunk_content("canon", str(file_path), 0)
        assert content is not None
        assert "tactical" in content
        assert "humor" in content
        assert len(content) == len(_CANON_BODY.strip())
    finally:
        index.close()


def test_get_chunk_content_returns_none_for_missing(tmp_path: Path) -> None:
    """get_chunk_content() returns None for an absent key."""
    index = _make_index(tmp_path)
    try:
        result = index.get_chunk_content("canon", "/nonexistent/path.md", 0)
        assert result is None
    finally:
        index.close()


def test_hash_skip_produces_zero_writes_on_rerun(tmp_path: Path) -> None:
    """MemoryStore.sync_dir skips unchanged files via hash check."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory_file(memory_dir / "001-test.md", body="some test body")

    index = _make_index(tmp_path)
    memory = MemoryStore(index=index, config=_STORE_CONFIG)
    try:
        first = memory.sync_dir(memory_dir)
        assert first == 1, f"expected 1 file indexed on first run, got {first}"

        second = memory.sync_dir(memory_dir)
        assert second == 0, f"expected 0 files indexed on second run (hash-skip), got {second}"
    finally:
        index.close()


def _write_memory_file(path: Path, *, body: str) -> None:
    fm = {
        "id": "test-1",
        "memory_kind": "user",
        "created": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(
        f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{body}\n",
        encoding="utf-8",
    )


def test_fts5_search_finds_indexed_entry(tmp_path: Path) -> None:
    """IndexStore FTS5-only path returns results for a synced memory artifact."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    expected_path = memory_dir / "001-test.md"
    _write_memory_file(expected_path, body="Finch the robot dog test")

    config = make_settings(
        memory=SETTINGS.memory.model_copy(
            update={
                "search_backend": "fts5",
                "embedding_provider": "none",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    index = IndexStore(config=config, db_path=tmp_path / "search.db")
    memory = MemoryStore(index=index, config=config)
    try:
        memory.sync_dir(memory_dir)
        results = index.search("Finch robot", sources=[MEMORY_SOURCE], limit=5)
        assert len(results) > 0, "FTS5 search returned no results for a synced artifact"
        assert results[0].path == str(expected_path), (
            f"expected result path {expected_path!s}, got {results[0].path!r}"
        )
    finally:
        index.close()
