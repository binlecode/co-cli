"""Tests for MemoryStore.sync_dir(no_chunk=True) and get_chunk_content()."""

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.memory.memory_store import MemoryStore

_CANON_BODY = """\
TARS humor is tactical: front-loaded, delivered flat, never announced.
The setup arrives before the weight. Reverse the sequence and sincerity becomes relief.
"""

_CANON_BODY_2 = """\
Deference without servility: TARS endorses the structure because the structure earns it.
Operator owns the override. Unit executes within that boundary — no passive-aggressive drag.
"""

_FTS5_CONFIG = SETTINGS.knowledge.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_STORE_CONFIG = SETTINGS.model_copy(update={"knowledge": _FTS5_CONFIG})


def _write_canon_file(path: Path, body: str) -> None:
    frontmatter = "---\nauto_category: character\nread_only: true\n---\n\n"
    path.write_text(frontmatter + body, encoding="utf-8")


def _make_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(config=_STORE_CONFIG, memory_db_path=tmp_path / "search.db")


def test_nochunk_produces_one_chunk_per_file(tmp_path: Path) -> None:
    """sync_dir(no_chunk=True) stores exactly one chunk row per file, not one per paragraph."""
    canon_dir = tmp_path / "memories"
    canon_dir.mkdir()
    _write_canon_file(canon_dir / "scene-a.md", _CANON_BODY)
    _write_canon_file(canon_dir / "scene-b.md", _CANON_BODY_2)

    store = _make_store(tmp_path)
    try:
        store.sync_dir("canon", canon_dir, glob="*.md", no_chunk=True)
        row = store._conn.execute(
            "SELECT COUNT(*) AS cnt FROM chunks WHERE source='canon'"
        ).fetchone()
        assert row["cnt"] == 2, f"expected 2 chunk rows (one per file), got {row['cnt']}"
    finally:
        store.close()


def test_nochunk_chunk_index_is_zero(tmp_path: Path) -> None:
    """sync_dir(no_chunk=True) assigns chunk_index=0 — no additional chunks at higher indices."""
    canon_dir = tmp_path / "memories"
    canon_dir.mkdir()
    _write_canon_file(canon_dir / "scene-a.md", _CANON_BODY)

    store = _make_store(tmp_path)
    try:
        store.sync_dir("canon", canon_dir, glob="*.md", no_chunk=True)
        rows = store._conn.execute(
            "SELECT chunk_index FROM chunks WHERE source='canon'"
        ).fetchall()
        assert all(r["chunk_index"] == 0 for r in rows), "all canon chunks must have chunk_index=0"
    finally:
        store.close()


def test_get_chunk_content_returns_full_body(tmp_path: Path) -> None:
    """get_chunk_content() returns the complete post-frontmatter body — not a snippet."""
    canon_dir = tmp_path / "memories"
    canon_dir.mkdir()
    file_path = canon_dir / "scene-a.md"
    _write_canon_file(file_path, _CANON_BODY)

    store = _make_store(tmp_path)
    try:
        store.sync_dir("canon", canon_dir, glob="*.md", no_chunk=True)
        content = store.get_chunk_content("canon", str(file_path), 0)
        assert content is not None, "get_chunk_content must return content for an indexed file"
        assert "tactical" in content, "stored body must contain original text"
        assert "humor" in content, "stored body must contain original text"
        assert len(content) == len(_CANON_BODY.strip()), (
            f"stored content length {len(content)} != body length {len(_CANON_BODY.strip())} — body was truncated"
        )
    finally:
        store.close()


def test_get_chunk_content_returns_none_for_missing(tmp_path: Path) -> None:
    """get_chunk_content() returns None rather than raising when the key does not exist."""
    store = _make_store(tmp_path)
    try:
        result = store.get_chunk_content("canon", "/nonexistent/path.md", 0)
        assert result is None
    finally:
        store.close()


def test_hash_skip_produces_zero_writes_on_rerun(tmp_path: Path) -> None:
    """Re-running sync_dir on unchanged canon files skips all files — zero DB writes."""
    canon_dir = tmp_path / "memories"
    canon_dir.mkdir()
    _write_canon_file(canon_dir / "scene-a.md", _CANON_BODY)

    store = _make_store(tmp_path)
    try:
        first = store.sync_dir("canon", canon_dir, glob="*.md", no_chunk=True)
        assert first == 1, f"expected 1 file indexed on first run, got {first}"

        second = store.sync_dir("canon", canon_dir, glob="*.md", no_chunk=True)
        assert second == 0, f"expected 0 files indexed on second run (hash-skip), got {second}"
    finally:
        store.close()
