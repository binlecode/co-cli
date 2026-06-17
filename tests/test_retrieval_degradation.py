"""Recall degradation signal — IndexStore.search reports HOW it answered.

Drives a real IndexStore configured to force each degraded branch (no mocking
of internals):
  - embedder unreachable in hybrid mode  -> SEMANTIC_UNAVAILABLE
  - reranker unreachable in hybrid mode  -> RERANK_UNAVAILABLE
  - both unreachable                     -> both (they co-occur on one query)
  - fully healthy hybrid                 -> empty set (needs live TEI)
"""

import socket
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.index.store import IndexStore, RecallDegradation
from co_cli.memory.service import reindex, save_memory_item


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


_EMBED_URL = SETTINGS.memory.embed_api_url
_RERANK_URL = SETTINGS.memory.cross_encoder_reranker_url


def _url_port(url: str | None) -> int | None:
    if not url:
        return None
    return int(url.rsplit(":", 1)[1])


_TEI_UP = (
    _RERANK_URL is not None
    and _port_open("127.0.0.1", _url_port(_EMBED_URL))
    and _port_open("127.0.0.1", _url_port(_RERANK_URL))
)
_needs_tei = pytest.mark.skipif(not _TEI_UP, reason="TEI embed/rerank services not reachable")

_DEAD_URL = "http://127.0.0.1:1"


def _seed(memory_dir: Path, index: IndexStore, *, title: str, body: str) -> None:
    r = save_memory_item(memory_dir, content=body, memory_kind="note", title=title)
    reindex(
        index,
        r.path,
        r.content,
        r.markdown_content,
        r.frontmatter_dict,
        r.filename_stem,
        chunk_tokens=600,
        chunk_overlap_tokens=80,
    )


def _hybrid_settings(*, embed_url: str | None, reranker_url: str | None):
    return SETTINGS.model_copy(
        update={
            "memory": SETTINGS.memory.model_copy(
                update={
                    "search_backend": "hybrid",
                    "embedding_provider": "tei",
                    "embed_api_url": embed_url,
                    "cross_encoder_reranker_url": reranker_url,
                }
            )
        }
    )


def test_semantic_unavailable_when_embedder_down(tmp_path: Path) -> None:
    """A hybrid query with a dead embedder reports SEMANTIC_UNAVAILABLE."""
    settings = _hybrid_settings(embed_url=_DEAD_URL, reranker_url=None)
    index = IndexStore(config=settings, db_path=tmp_path / "search.db")
    try:
        _seed(tmp_path / "memory", index, title="note", body="event pipeline columnar analytics")
        hits, degraded = index.search("event pipeline", limit=5)
        assert hits, "lexical fallback must still return the hit"
        assert RecallDegradation.SEMANTIC_UNAVAILABLE in degraded
        assert RecallDegradation.RERANK_UNAVAILABLE not in degraded
    finally:
        index.close()


def test_rerank_unavailable_when_reranker_down(tmp_path: Path) -> None:
    """A hybrid query whose reranker is unreachable reports RERANK_UNAVAILABLE.

    Embedder is also dead here (no live TEI required), so both modes co-occur on
    the one query — exercising the accumulating frozenset, not a single enum.
    """
    settings = _hybrid_settings(embed_url=_DEAD_URL, reranker_url=_DEAD_URL)
    index = IndexStore(config=settings, db_path=tmp_path / "search.db")
    try:
        _seed(tmp_path / "memory", index, title="note", body="event pipeline columnar analytics")
        hits, degraded = index.search("event pipeline", limit=5)
        assert hits, "lexical fallback must still return the hit"
        assert RecallDegradation.RERANK_UNAVAILABLE in degraded
        assert RecallDegradation.SEMANTIC_UNAVAILABLE in degraded
    finally:
        index.close()


@_needs_tei
def test_healthy_hybrid_reports_no_degradation(tmp_path: Path) -> None:
    """A fully healthy hybrid query reports an empty degradation set."""
    settings = _hybrid_settings(embed_url=_EMBED_URL, reranker_url=_RERANK_URL)
    index = IndexStore(config=settings, db_path=tmp_path / "search.db")
    try:
        _seed(tmp_path / "memory", index, title="note", body="event pipeline columnar analytics")
        hits, degraded = index.search("event pipeline columnar", limit=5)
        assert hits, "expected a hit"
        assert degraded == frozenset(), f"healthy recall must report none, got {degraded}"
    finally:
        index.close()
