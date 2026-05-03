"""Tests for _sync_canon_store() bootstrap wiring."""

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.bootstrap.core import _sync_canon_store
from co_cli.memory.memory_store import MemoryStore

_FTS5_CONFIG = SETTINGS.knowledge.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_STORE_CONFIG = SETTINGS.model_copy(update={"knowledge": _FTS5_CONFIG})


class _SilentFrontend:
    def on_status(self, msg: str) -> None:
        pass


def _make_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(config=_STORE_CONFIG, memory_db_path=tmp_path / "search.db")


def test_sync_canon_store_indexes_real_tars_memories(tmp_path: Path) -> None:
    """_sync_canon_store writes tars canon files into FTS so search returns results."""
    config = SETTINGS.model_copy(update={"personality": "tars"})
    store = _make_store(tmp_path)
    try:
        _sync_canon_store(store, config, _SilentFrontend())
        results = store.search("humor deadpan", sources=["canon"])
        assert len(results) >= 1, (
            "expected at least 1 canon result for 'humor deadpan' after _sync_canon_store"
        )
    finally:
        store.close()


def test_sync_canon_store_noop_when_store_is_none() -> None:
    """_sync_canon_store silently returns when store=None — no AttributeError on None."""
    config = SETTINGS.model_copy(update={"personality": "tars"})
    _sync_canon_store(None, config, _SilentFrontend())


def test_sync_canon_store_noop_when_personality_none(tmp_path: Path) -> None:
    """_sync_canon_store writes zero rows when personality is empty — canon gated on role."""
    config = SETTINGS.model_copy(update={"personality": None})
    store = _make_store(tmp_path)
    try:
        _sync_canon_store(store, config, _SilentFrontend())
        row = store._conn.execute(
            "SELECT COUNT(*) AS cnt FROM chunks WHERE source='canon'"
        ).fetchone()
        assert row["cnt"] == 0, f"expected 0 canon chunks when personality=None, got {row['cnt']}"
    finally:
        store.close()
