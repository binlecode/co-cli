"""Reranker input is truncated to the configured per-candidate char budget.

Bounds the cross-encoder's input so rerank latency stays predictable regardless of
chunk size. The reranker HTTP call is disabled here (as in the other retrieval
tests); truncation is observed at its source — the text list built by
``_fetch_reranker_texts`` before the call.
"""

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.index.store import IndexStore
from co_cli.memory.service import reindex, save_memory_item

_CONFIG = SETTINGS.memory.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_SETTINGS = SETTINGS.model_copy(update={"memory": _CONFIG})


def test_reranker_input_truncated_to_char_budget(tmp_path: Path) -> None:
    """Each candidate's reranker text is capped at the configured content budget."""
    budget = _SETTINGS.memory.rerank_text_char_budget
    index = IndexStore(config=_SETTINGS, db_path=tmp_path / "search.db")
    try:
        long_content = "event pipeline columnar analytics review " * 200
        r = save_memory_item(
            tmp_path / "memory",
            content=long_content,
            memory_kind="note",
            title="long note",
        )
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

        max_chunk_chars = index._retrieval._conn.execute(
            "SELECT max(length(content)) FROM chunks"
        ).fetchone()[0]
        assert max_chunk_chars > budget, "fixture must contain an over-budget chunk"

        hits = index.search("event pipeline", limit=10)
        assert hits, "expected at least one hit"

        texts = index._retrieval._fetch_reranker_texts(hits)
        assert texts, "expected reranker texts"
        for hit, text in zip(hits, texts, strict=True):
            title_len = len(hit.title or "")
            assert len(text) <= title_len + 1 + budget
    finally:
        index.close()
