"""Consolidated E2E tests for test_flow_memory_search."""

from pathlib import Path

import yaml
from tests._settings import make_settings

from co_cli.memory.knowledge_store import KnowledgeStore


def _write_knowledge_file(path: Path, *, body: str):
    fm = {
        "id": "test-1",
        "kind": "knowledge",
        "artifact_kind": "preference",
        "created": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(
        f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{body}\n",
        encoding="utf-8",
    )


def test_fts5_search_finds_indexed_entry(tmp_path: Path):
    """KnowledgeStore FTS5-only path must return results for a synced artifact."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    _write_knowledge_file(knowledge_dir / "001-test.md", body="Finch the robot dog test")

    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "fts5",
                "embedding_provider": "none",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    try:
        store.sync_dir("knowledge", knowledge_dir)
        results = store.search("Finch robot", source="knowledge", limit=5)
        assert len(results) > 0
    finally:
        store.close()
