"""Tests for _sync_canon_store() bootstrap wiring."""

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.bootstrap.core import _sync_canon_store
from co_cli.display.core import TerminalFrontend
from co_cli.index.store import IndexStore

_FTS5_CONFIG = SETTINGS.memory.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_STORE_CONFIG = SETTINGS.model_copy(update={"memory": _FTS5_CONFIG})


def _make_store(tmp_path: Path) -> IndexStore:
    return IndexStore(config=_STORE_CONFIG, db_path=tmp_path / "search.db")


def test_sync_canon_store_indexes_real_tars_memories(tmp_path: Path) -> None:
    """_sync_canon_store writes tars canon files into FTS so search returns results."""
    config = SETTINGS.model_copy(update={"personality": "tars"})
    store = _make_store(tmp_path)
    try:
        _sync_canon_store(store, config, TerminalFrontend())
        results, _ = store.search("humor deadpan", sources=["canon"])
        assert len(results) >= 1, (
            "expected at least 1 canon result for 'humor deadpan' after _sync_canon_store"
        )
    finally:
        store.close()


def test_sync_canon_store_noop_when_personality_none(tmp_path: Path) -> None:
    """_sync_canon_store writes zero rows when personality is empty — canon gated on role."""
    config = SETTINGS.model_copy(update={"personality": None})
    store = _make_store(tmp_path)
    try:
        _sync_canon_store(store, config, TerminalFrontend())
        names = store.list_titles_by_source("canon")
        assert not names, f"expected no canon docs when personality=None, got {names}"
    finally:
        store.close()
