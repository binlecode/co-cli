"""Functional tests for bootstrap-sequence behaviors (real components, direct API calls)."""

import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from co_cli.bootstrap._bootstrap import restore_session
from co_cli.context._session import is_fresh, load_session, new_session, save_session
from co_cli.knowledge._index_store import KnowledgeIndex
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState, CoRuntimeState
from co_cli.context._history import OpeningContextState, SafetyState
from co_cli.tools._shell_backend import ShellBackend


def _write_memory_file(path: Path, *, mem_id: int, body: str) -> None:
    path.write_text(
        (
            "---\n"
            f"id: {mem_id}\n"
            "created: '2026-03-01T00:00:00+00:00'\n"
            "kind: memory\n"
            "tags:\n"
            "- wakeup\n"
            "---\n\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )


def _write_article_file(path: Path, *, art_id: int, body: str) -> None:
    fm = {
        "id": art_id,
        "kind": "article",
        "created": "2026-01-01T00:00:00+00:00",
        "tags": [],
        "decay_protected": True,
        "origin_url": "https://example.com/test",
    }
    path.write_text(
        f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{body}\n",
        encoding="utf-8",
    )


def test_knowledge_sync_writes_to_index_and_fresh_session_is_restored(tmp_path: Path) -> None:
    """sync_dir() indexes files into the index; a just-saved session is fresh and restorable."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    mem_file = memory_dir / "001-wakeup-memory.md"
    _write_memory_file(mem_file, mem_id=1, body="Wakeup sync writes this entry to the index.")

    session_path = tmp_path / "session.json"
    session_data = new_session()
    save_session(session_path, session_data)

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="fts5", knowledge_reranker_provider="none"))
    try:
        count = idx.sync_dir("memory", memory_dir, kind_filter="memory")
        assert count >= 1, "sync_dir must index at least one file"

        results = idx.search("wakeup sync", source="memory", limit=5)
        assert results, "Synced knowledge must be searchable in the real index"
        assert any(r.path == str(mem_file) for r in results)

        loaded = load_session(session_path)
        assert loaded is not None
        assert is_fresh(loaded, ttl_minutes=60), "A just-saved session must be fresh"
        assert loaded["session_id"] == session_data["session_id"]
    finally:
        idx.close()


def test_two_pass_sync_partitions_by_kind(tmp_path: Path) -> None:
    """sync_dir() with kind_filter routes memory files to source='memory' and articles to source='library'."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    library_dir = tmp_path / "library"
    library_dir.mkdir(parents=True)

    mem_file = memory_dir / "001-mem.md"
    _write_memory_file(mem_file, mem_id=1, body="Memory content for partition test.")

    art_file = library_dir / "002-article.md"
    _write_article_file(art_file, art_id=2, body="Article content for partition test.")

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="fts5", knowledge_reranker_provider="none"))
    try:
        idx.sync_dir("memory", memory_dir, kind_filter="memory")
        idx.sync_dir("library", library_dir, kind_filter="article")

        mem_results = idx.search("partition test", source="memory", limit=5)
        assert any(r.path == str(mem_file) for r in mem_results), \
            "Memory file must be searchable under source='memory'"

        art_results = idx.search("partition test", source="library", limit=5)
        assert any(r.path == str(art_file) for r in art_results), \
            "Article file must be searchable under source='library'"
    finally:
        idx.close()


def test_index_disabled_when_sync_fails(tmp_path: Path) -> None:
    """When sync_dir() fails due to a closed index, setting knowledge_index to None disables it."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    _write_memory_file(memory_dir / "001-mem.md", mem_id=1, body="Should fail to sync.")

    idx = KnowledgeIndex(config=CoConfig(knowledge_db_path=tmp_path / "search.db", knowledge_search_backend="fts5", knowledge_reranker_provider="none"))
    idx.close()

    deps = CoDeps(
        services=CoServices(shell=ShellBackend(), knowledge_index=idx),
        config=CoConfig(knowledge_search_backend="fts5"),
    )

    sync_failed = False
    try:
        deps.services.knowledge_index.sync_dir("memory", memory_dir, kind_filter="memory")
    except Exception:
        sync_failed = True
        deps.services.knowledge_index = None

    assert sync_failed, "sync_dir on a closed index must raise an exception"
    assert deps.services.knowledge_index is None, \
        "knowledge_index must be set to None after sync failure"


def _make_deps(session_path: Path) -> CoDeps:
    config = CoConfig(session_path=session_path, session_ttl_minutes=60)
    services = CoServices(shell=ShellBackend())
    runtime = CoRuntimeState(opening_ctx_state=OpeningContextState(), safety_state=SafetyState())
    return CoDeps(services=services, config=config, session=CoSessionState(), runtime=runtime)


def test_stale_session_creates_new_session_id(tmp_path: Path) -> None:
    """restore_session() with a stale on-disk session must create a new session_id in deps.config."""
    session_path = tmp_path / "session.json"
    stale = new_session()
    stale_id = stale["session_id"]
    stale["last_used_at"] = (datetime.now(timezone.utc) - timedelta(minutes=180)).isoformat()
    save_session(session_path, stale)

    deps = _make_deps(session_path)
    from co_cli.display import TerminalFrontend
    frontend = TerminalFrontend()
    restore_session(deps, frontend)

    assert deps.config.session_id != stale_id, \
        "restore_session() must assign a new session_id when the on-disk session is stale"
    assert deps.config.session_id != "", "restore_session() must set a non-empty session_id"
    # New session file must have been written with the fresh ID
    on_disk = load_session(session_path)
    assert on_disk["session_id"] == deps.config.session_id, \
        "restore_session() must persist the new session_id to disk"
