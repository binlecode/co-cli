"""Functional tests for bootstrap-sequence behaviors (real components, direct API calls)."""

import asyncio
import os
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from co_cli.config import ModelConfig
from co_cli.bootstrap._bootstrap import _discover_knowledge_backend, _sync_knowledge_store, _resolve_reranker, restore_session
from co_cli.context._types import SafetyState
from co_cli.context._session import load_session, new_session, save_session
from co_cli.deps import CoDeps, CoConfig, CoRuntimeState, CoSessionState
from co_cli.display._core import TerminalFrontend
from co_cli.knowledge._store import KnowledgeStore
from co_cli.tools._shell_backend import ShellBackend


def _make_deps(
    tmp_path: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
    session_ttl_minutes: int = 60,
    memory_dir: Path | None = None,
    library_dir: Path | None = None,
    mcp_servers: dict | None = None,
) -> CoDeps:
    config = CoConfig(
        session_path=tmp_path / "session.json",
        session_ttl_minutes=session_ttl_minutes,
        memory_dir=memory_dir or tmp_path / "memory",
        library_dir=library_dir or tmp_path / "library",
        mcp_servers=mcp_servers if mcp_servers is not None else {},
    )
    runtime = CoRuntimeState(safety_state=SafetyState())
    return CoDeps(shell=ShellBackend(), knowledge_store=knowledge_store, config=config, session=CoSessionState(), runtime=runtime)


def _write_memory_file(path: Path, *, mem_id: int, body: str) -> None:
    path.write_text(
        (
            "---\n"
            f"id: {mem_id}\n"
            "created: '2026-03-01T00:00:00+00:00'\n"
            "kind: memory\n"
            "tags:\n"
            "- test\n"
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



def test_sync_knowledge_store_indexes_memory_and_article(tmp_path: Path) -> None:
    """_sync_knowledge_store reconciles memory and article files into the store."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    _write_memory_file(memory_dir / "001-test-mem.md", mem_id=1, body="Bootstrap memory content for sync test.")
    _write_article_file(library_dir / "002-test-art.md", art_id=2, body="Bootstrap article content for sync test.")

    config = CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_search_backend="fts5",
        knowledge_cross_encoder_reranker_url=None,
        memory_dir=memory_dir,
        library_dir=library_dir,
        session_path=tmp_path / "session.json",
    )
    store = _discover_knowledge_backend(config, TerminalFrontend())
    assert store is not None, "_discover_knowledge_backend must return a store for fts5"
    try:
        store = _sync_knowledge_store(store, config, TerminalFrontend())
        assert store is not None, "_sync_knowledge_store must not disable the store on success"

        mem_results = store.search("Bootstrap memory content", source="memory", limit=5)
        assert any("001-test-mem.md" in r.path for r in mem_results), \
            "Memory file must be findable under source='memory' after sync"

        art_results = store.search("Bootstrap article content", source="library", limit=5)
        assert any("002-test-art.md" in r.path for r in art_results), \
            "Article file must be findable under source='library' after sync"
    finally:
        if store is not None:
            store.close()


def test_discover_knowledge_backend_returns_none_on_grep(tmp_path: Path) -> None:
    """_discover_knowledge_backend returns None when backend is grep — no store needed."""
    config = CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_search_backend="grep",
        session_path=tmp_path / "session.json",
    )
    result = _discover_knowledge_backend(config, TerminalFrontend())
    assert result is None, "_discover_knowledge_backend must return None for grep backend"


def test_discover_knowledge_backend_degrades_hybrid_to_fts5_when_embedder_unavailable(tmp_path: Path) -> None:
    """Hybrid discovery must degrade to FTS5 when the embedder is unreachable at startup."""
    config = CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_search_backend="hybrid",
        knowledge_embedding_provider="tei",
        knowledge_embed_api_url="http://127.0.0.1:1/embed",
        knowledge_cross_encoder_reranker_url=None,
    )

    store = _discover_knowledge_backend(config, TerminalFrontend())
    try:
        assert store is not None, "Discovery must return a store (degraded to fts5), not None"
        assert store.backend == "fts5", \
            "Discovery must degrade hybrid to fts5 when embedder is unavailable"
    finally:
        if store is not None:
            store.close()


def test_restore_session_fresh_returns_same_id(tmp_path: Path) -> None:
    """restore_session() with a fresh on-disk session must restore the same session_id into deps."""
    session_path = tmp_path / "session.json"
    session_data = new_session()
    save_session(session_path, session_data)
    original_id = session_data["session_id"]

    deps = _make_deps(tmp_path)
    restore_session(deps, TerminalFrontend())

    assert deps.session.session_id == original_id, \
        "restore_session() must restore the on-disk session_id when the session is still fresh"


def test_restore_session_stale_creates_new_id(tmp_path: Path) -> None:
    """restore_session() with a stale on-disk session must create and persist a new session_id."""
    session_path = tmp_path / "session.json"
    stale = new_session()
    stale_id = stale["session_id"]
    stale["last_used_at"] = (datetime.now(timezone.utc) - timedelta(minutes=180)).isoformat()
    save_session(session_path, stale)

    deps = _make_deps(tmp_path)
    restore_session(deps, TerminalFrontend())

    assert deps.session.session_id != stale_id, \
        "restore_session() must not reuse a stale session_id"
    assert deps.session.session_id != ""
    on_disk = load_session(session_path)
    assert on_disk["session_id"] == deps.session.session_id, \
        "restore_session() must persist the new session_id to disk"


def test_resolve_reranker_nothing_configured_returns_unchanged() -> None:
    """No reranker configured → config unchanged, no status messages."""
    config = CoConfig(knowledge_cross_encoder_reranker_url=None, knowledge_llm_reranker=None)
    statuses: list[str] = []
    resolved = _resolve_reranker(config, statuses)
    assert resolved.knowledge_cross_encoder_reranker_url is None
    assert resolved.knowledge_llm_reranker is None
    assert statuses == []


def test_resolve_reranker_tei_unavailable_nulls_url() -> None:
    """TEI cross-encoder at a dead port → URL nulled, degradation status emitted."""
    config = CoConfig(
        knowledge_cross_encoder_reranker_url="http://127.0.0.1:19999",
        knowledge_llm_reranker=None,
    )
    statuses: list[str] = []
    resolved = _resolve_reranker(config, statuses)
    assert resolved.knowledge_cross_encoder_reranker_url is None
    assert any("cross-encoder" in s.lower() or "tei" in s.lower() for s in statuses)


def test_resolve_reranker_llm_unavailable_nulls_reranker() -> None:
    """LLM reranker with gemini provider but no API key → check_reranker_llm returns error → reranker nulled."""
    config = CoConfig(
        knowledge_cross_encoder_reranker_url=None,
        knowledge_llm_reranker=ModelConfig(provider="gemini", model="gemini-2.0-flash"),
        llm_provider="gemini",
        llm_api_key=None,
    )
    statuses: list[str] = []
    resolved = _resolve_reranker(config, statuses)
    assert resolved.knowledge_llm_reranker is None
    assert any("llm" in s.lower() or "reranker" in s.lower() for s in statuses)


def test_resolve_reranker_llm_ollama_unreachable_degrades() -> None:
    """LLM reranker with Ollama provider but unreachable host → reranker nulled (warn != ok)."""
    config = CoConfig(
        knowledge_cross_encoder_reranker_url=None,
        knowledge_llm_reranker=ModelConfig(provider="ollama-openai", model="reranker-model"),
        llm_provider="ollama-openai",
        llm_host="http://localhost:1",
    )
    statuses: list[str] = []
    resolved = _resolve_reranker(config, statuses)
    assert resolved.knowledge_llm_reranker is None
    assert any("llm" in s.lower() or "reranker" in s.lower() for s in statuses)


def test_resolve_reranker_both_unavailable_degrades_independently() -> None:
    """TEI dead + LLM reranker with no API key → both nulled, two separate status messages."""
    config = CoConfig(
        knowledge_cross_encoder_reranker_url="http://127.0.0.1:19999",
        knowledge_llm_reranker=ModelConfig(provider="gemini", model="gemini-2.0-flash"),
        llm_provider="gemini",
        llm_api_key=None,
    )
    statuses: list[str] = []
    resolved = _resolve_reranker(config, statuses)
    assert resolved.knowledge_cross_encoder_reranker_url is None
    assert resolved.knowledge_llm_reranker is None
    assert len(statuses) == 2


def test_skill_loading_project_skill_registered(tmp_path: Path) -> None:
    """Project skill directory with one valid skill: skill appears in loaded commands."""
    from co_cli.commands._commands import _load_skills, get_skill_registry
    from co_cli.config import settings

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_content = (
        "---\n"
        "description: Test skill for bootstrap functional tests\n"
        "---\n\n"
        "Perform a test action.\n"
    )
    (skills_dir / "test-bootstrap-skill.md").write_text(skill_content, encoding="utf-8")

    skill_commands = _load_skills(skills_dir, settings=settings)

    assert "test-bootstrap-skill" in skill_commands, (
        "Project skill must appear in skill_commands after _load_skills"
    )
    assert len(get_skill_registry(skill_commands)) >= 1, (
        "skill_count must be at least 1 when a valid project skill is loaded"
    )


def test_restore_session_corrupt_json_creates_new_session(tmp_path: Path) -> None:
    """restore_session() with corrupt session.json creates a new session instead of crashing."""
    session_path = tmp_path / "session.json"
    session_path.write_text("not valid json{{{", encoding="utf-8")

    deps = _make_deps(tmp_path)
    result = restore_session(deps, TerminalFrontend())

    assert isinstance(result, dict), "restore_session() must return a session dict even with corrupt file"
    assert deps.session.session_id != "", "session_id must be set after corrupt file recovery"


def test_restore_session_oserror_on_save_does_not_raise(tmp_path: Path) -> None:
    """restore_session() must not raise when save_session() fails due to a permissions error."""
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    os.chmod(readonly_dir, 0o555)
    try:
        session_path = readonly_dir / "session.json"
        config = CoConfig(
            session_path=session_path,
            session_ttl_minutes=60,
            memory_dir=tmp_path / "memory",
            library_dir=tmp_path / "library",
            mcp_servers={},
        )
        runtime = CoRuntimeState(safety_state=SafetyState())
        deps = CoDeps(shell=ShellBackend(), knowledge_store=None, config=config, session=CoSessionState(), runtime=runtime)
        result = restore_session(deps, TerminalFrontend())
        assert isinstance(result, dict), "restore_session() must return a session dict even when save fails"
        assert deps.session.session_id != "", "session_id must be set in deps even when save fails"
    finally:
        os.chmod(readonly_dir, 0o755)
