"""Tests for KnowledgeStore._chunks_like_search match-density ranking."""

from pathlib import Path

from tests._settings import make_settings

from co_cli.memory.chunker import Chunk
from co_cli.memory.knowledge_store import KnowledgeStore, _chunks_like_search
from co_cli.memory.search_util import normalize_bm25


def _make_store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(config=make_settings(), knowledge_db_path=tmp_path / "search.db")


def _index_doc(store: KnowledgeStore, path: str, content: str) -> None:
    """Index a minimal doc as a single chunk."""
    store.index(source="knowledge", kind="note", path=path, title=path, content=content)
    store.index_chunks(
        "knowledge", path, [Chunk(index=0, content=content, start_line=0, end_line=0)]
    )


# ---------------------------------------------------------------------------
# Unit: _chunks_like_search ranking by match density
# ---------------------------------------------------------------------------


def test_like_fallback_ranks_by_match_density(tmp_path: Path) -> None:
    """Row matching 2 query tokens must have a more negative rank than row matching 1 token."""
    store = _make_store(tmp_path)
    try:
        # artifact_a content contains 'alpha' and 'beta'; artifact_b contains only 'alpha'
        _index_doc(store, "artifact_a", "The alpha and beta techniques are described here.")
        _index_doc(store, "artifact_b", "The alpha technique is useful for processing.")

        rows = _chunks_like_search(
            store._conn,
            ["alpha", "beta"],
            sources=None,
            kind=None,
            created_after=None,
            created_before=None,
            limit=10,
        )

        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

        ranks = {row["path"]: row["rank"] for row in rows}

        # artifact_a matches both tokens → rank = -2; artifact_b matches one → rank = -1
        assert ranks["artifact_a"] < ranks["artifact_b"], (
            f"artifact_a rank {ranks['artifact_a']} should be more negative "
            f"than artifact_b rank {ranks['artifact_b']}"
        )

        score_a = normalize_bm25(ranks["artifact_a"])
        score_b = normalize_bm25(ranks["artifact_b"])
        assert score_a > score_b, (
            f"artifact_a score {score_a:.4f} should exceed artifact_b score {score_b:.4f}"
        )
    finally:
        store.close()


def test_like_fallback_row_shape(tmp_path: Path) -> None:
    """LIKE fallback rows expose all columns consumed by the SearchResult mapping in _fts_search."""
    store = _make_store(tmp_path)
    try:
        _index_doc(store, "shape_doc", "sample content for shape check.")

        rows = _chunks_like_search(
            store._conn,
            ["sample"],
            sources=None,
            kind=None,
            created_after=None,
            created_before=None,
            limit=10,
        )

        assert len(rows) == 1
        required = {
            "source",
            "path",
            "snippet",
            "rank",
            "chunk_index",
            "start_line",
            "end_line",
            "kind",
            "title",
            "category",
            "created",
            "updated",
            "provenance",
            "certainty",
            "source_ref",
            "artifact_id",
        }
        actual = set(rows[0].keys())
        assert required.issubset(actual), f"Missing columns: {required - actual}"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Score-sort: fallback rows sort correctly when the pipeline applies normalize_bm25
# ---------------------------------------------------------------------------


def test_like_fallback_results_sort_by_match_density(tmp_path: Path) -> None:
    """Rows from _chunks_like_search, sorted by normalized score, put the higher-match doc first.

    This mirrors what _fts_search does: it calls normalize_bm25(row['rank']) and sorts descending.
    """
    store = _make_store(tmp_path)
    try:
        _index_doc(store, "artifact_a", "The alpha and beta techniques are described here.")
        _index_doc(store, "artifact_b", "The alpha technique is useful for processing.")

        rows = _chunks_like_search(
            store._conn,
            ["alpha", "beta"],
            sources=None,
            kind=None,
            created_after=None,
            created_before=None,
            limit=10,
        )

        assert len(rows) == 2
        sorted_rows = sorted(rows, key=lambda r: normalize_bm25(r["rank"]), reverse=True)
        assert sorted_rows[0]["path"] == "artifact_a", (
            f"artifact_a should sort first by score but got: {[r['path'] for r in sorted_rows]}"
        )
        assert normalize_bm25(sorted_rows[0]["rank"]) > normalize_bm25(sorted_rows[1]["rank"]), (
            f"First score {normalize_bm25(sorted_rows[0]['rank']):.4f} should exceed "
            f"second score {normalize_bm25(sorted_rows[1]['rank']):.4f}"
        )
    finally:
        store.close()
