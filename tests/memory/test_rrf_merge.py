"""Tests for _rrf_merge doc-level score collapse (max, not sum)."""

from pathlib import Path

import pytest
from tests._settings import make_settings

from co_cli.memory.knowledge_store import KnowledgeStore, SearchResult


def _chunk(path: str, chunk_index: int, score: float = 0.5) -> SearchResult:
    return SearchResult(
        source="knowledge",
        kind="knowledge",
        path=path,
        title=path,
        snippet=f"snippet from {path} chunk {chunk_index}",
        score=score,
        category=None,
        created=None,
        updated=None,
        chunk_index=chunk_index,
    )


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")


def test_rrf_merge_max_not_sum_prevents_crowding(store: KnowledgeStore) -> None:
    """A short doc whose top chunk ranks first must beat a long doc with many lower-ranked chunks.

    With sum aggregation the long doc accumulates enough chunk scores to overtake
    the short doc despite no individual chunk ranking higher — a length bias.
    With max aggregation only the best chunk per doc counts, so ranking reflects
    relevance rather than document length.

    FTS list (rank order = list position):
      position 0: short-doc  chunk 0   → RRF score 1/61 ≈ 0.01639  (best chunk)
      position 1: long-doc   chunk 0   → RRF score 1/62 ≈ 0.01613
      position 2: long-doc   chunk 1   → RRF score 1/63
      position 3: long-doc   chunk 2   → RRF score 1/64
      position 4: long-doc   chunk 3   → RRF score 1/65
      position 5: long-doc   chunk 4   → RRF score 1/66

    sum(long-doc) ≈ 0.0781  >  sum(short-doc) ≈ 0.01639  → crowding bug
    max(long-doc) ≈ 0.01613 <  max(short-doc) ≈ 0.01639  → correct
    """
    fts_chunks = [
        _chunk("short-doc", chunk_index=0),
        _chunk("long-doc", chunk_index=0),
        _chunk("long-doc", chunk_index=1),
        _chunk("long-doc", chunk_index=2),
        _chunk("long-doc", chunk_index=3),
        _chunk("long-doc", chunk_index=4),
    ]

    results = store._hybrid_merge(fts_chunks, vec_chunks=[])

    paths = [r.path for r in results]
    assert paths[0] == "short-doc", (
        f"short-doc should rank first (most relevant top chunk) but got: {paths}"
    )


def test_rrf_merge_fts_and_vec_agreement_boosts_score(store: KnowledgeStore) -> None:
    """A doc appearing in both FTS and vec lists scores higher than one in only one list."""
    fts_chunks = [
        _chunk("both-doc", chunk_index=0),
        _chunk("fts-only-doc", chunk_index=0),
    ]
    vec_chunks = [
        _chunk("both-doc", chunk_index=0),
        _chunk("vec-only-doc", chunk_index=0),
    ]

    results = store._hybrid_merge(fts_chunks, vec_chunks=vec_chunks)

    paths = [r.path for r in results]
    assert paths[0] == "both-doc", (
        f"doc appearing in both FTS and vec should rank first but got: {paths}"
    )


def test_rrf_merge_winning_chunk_snippet_preserved(store: KnowledgeStore) -> None:
    """The snippet attached to the output result is from the highest-scoring chunk."""
    fts_chunks = [
        _chunk("doc-a", chunk_index=0),
        _chunk("doc-a", chunk_index=1),
        _chunk("doc-a", chunk_index=2),
    ]
    # Override snippet on the best (first-ranked) chunk so we can identify it.
    fts_chunks[0] = SearchResult(
        **{**fts_chunks[0].__dict__, "snippet": "WINNER_SNIPPET"},
    )

    results = store._hybrid_merge(fts_chunks, vec_chunks=[])

    assert len(results) == 1
    assert results[0].snippet == "WINNER_SNIPPET"
