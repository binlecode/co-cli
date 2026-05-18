"""Tests for canon decoupling — canon is doctrine, never returned by model-callable tools.

After the four-tier decomposition:
- Canon is indexed at bootstrap by `_sync_canon_store` for the personality system only.
- No model-callable tool (`memory_search`, `_search_artifacts`) returns canon hits.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.bootstrap.core import _sync_canon_store
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.core import TerminalFrontend
from co_cli.index.store import IndexStore
from co_cli.memory.store import MemoryStore
from co_cli.tools.memory.recall import _search_memory_items, memory_search
from co_cli.tools.shell_backend import ShellBackend

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


def _make_ctx(tmp_path: Path, *, personality: str | None) -> RunContext[CoDeps]:
    """RunContext with a real MemoryStore; canon indexed from real tars soul files when personality set."""
    store = _make_store(tmp_path)
    config = _STORE_CONFIG.model_copy(update={"personality": personality})
    if personality:
        _sync_canon_store(store, config, TerminalFrontend())
    memory = MemoryStore(index=store, config=config)
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        session=CoSessionState(),
        index_store=store,
        memory_store=memory,
        sessions_dir=tmp_path / "sessions",
        memory_dir=tmp_path / "memory",
    )
    return RunContext(deps=deps, model=None, usage=RunUsage())


def test_canon_still_indexed_at_bootstrap_for_personality_system(tmp_path: Path) -> None:
    """`_sync_canon_store` indexes canon under source='canon' so the personality system can read it.

    This is the legitimate consumer path — bodies stay in the FTS DB and the personality
    system reads them via IndexStore.get_chunk_content('canon', path, 0).
    """
    store = _make_store(tmp_path)
    config = _STORE_CONFIG.model_copy(update={"personality": "tars"})
    try:
        _sync_canon_store(store, config, TerminalFrontend())
        names = store.list_titles_by_source("canon")
        assert names, "expected canon docs indexed under source='canon' after _sync_canon_store"
    finally:
        store.close()


def test_search_memory_items_canon_kind_filter_returns_empty(tmp_path: Path) -> None:
    """Even when callers request kinds=['canon'], _search_memory_items returns []."""
    ctx = _make_ctx(tmp_path, personality="tars")
    try:
        hits = _search_memory_items(ctx, "humor deadpan", kinds=["canon"], limit=10)
        assert hits == [], f"kinds=['canon'] must return empty; got: {hits}"
    finally:
        ctx.deps.index_store.close()


@pytest.mark.asyncio
async def test_memory_search_all_channels_excludes_canon_hits(tmp_path: Path) -> None:
    """memory_search never returns canon kind in flat result list."""
    ctx = _make_ctx(tmp_path, personality="tars")
    try:
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_search(ctx, query="humor deadpan")
        results = result.metadata.get("results") or []
        canon_results = [r for r in results if r.get("kind") == "canon"]
        assert canon_results == [], (
            f"memory_search must not surface canon hits; got: {canon_results}"
        )
    finally:
        ctx.deps.index_store.close()
