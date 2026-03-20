"""Functional tests for bootstrap-sequence behaviors (real components, direct API calls)."""

import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path

from co_cli.agent import _build_system_prompt
from co_cli.bootstrap._banner import display_welcome_banner
from co_cli.bootstrap._bootstrap import resolve_knowledge_backend, restore_session, sync_knowledge
from co_cli.bootstrap._check import check_llm
from co_cli.context._history import OpeningContextState, SafetyState
from co_cli.context._session import load_session, new_session, save_session
from co_cli.deps import CoDeps, CoConfig, CoRuntimeState, CoServices, CoSessionState
from co_cli.display import TerminalFrontend, console
from co_cli.knowledge._index_store import KnowledgeIndex
from co_cli.tools._shell_backend import ShellBackend


def _make_deps(
    tmp_path: Path,
    *,
    knowledge_index: KnowledgeIndex | None = None,
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
    services = CoServices(shell=ShellBackend(), knowledge_index=knowledge_index)
    runtime = CoRuntimeState(opening_ctx_state=OpeningContextState(), safety_state=SafetyState())
    return CoDeps(services=services, config=config, session=CoSessionState(), runtime=runtime)


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


def test_check_llm_rejects_gemini_without_key() -> None:
    """check_llm returns error for gemini with no API key — the session must never start in this state."""
    config = CoConfig(llm_provider="gemini", llm_api_key=None)
    result = check_llm(config)
    assert result.status == "error"
    assert "LLM_API_KEY" in result.detail or "gemini" in result.detail.lower()


def test_build_system_prompt_assembles_non_empty() -> None:
    """_build_system_prompt must return a non-empty string — a blank prompt leaves the agent with no instructions."""
    config = CoConfig()
    result = _build_system_prompt("ollama-openai", "", config)
    assert result and len(result) > 50


def test_sync_knowledge_indexes_memory_and_article(tmp_path: Path) -> None:
    """sync_knowledge() routes memory and article files to the correct index sources and keeps the index active."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    _write_memory_file(memory_dir / "001-test-mem.md", mem_id=1, body="Bootstrap memory content for sync test.")
    _write_article_file(library_dir / "002-test-art.md", art_id=2, body="Bootstrap article content for sync test.")

    idx = KnowledgeIndex(config=CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_search_backend="fts5",
        knowledge_reranker_provider="none",
    ))
    try:
        deps = _make_deps(tmp_path, knowledge_index=idx, memory_dir=memory_dir, library_dir=library_dir)
        sync_knowledge(deps, TerminalFrontend())

        assert deps.services.knowledge_index is not None, "sync_knowledge must not disable the index on success"

        mem_results = idx.search("Bootstrap memory content", source="memory", limit=5)
        assert any("001-test-mem.md" in r.path for r in mem_results), \
            "Memory file must be findable under source='memory' after sync"

        art_results = idx.search("Bootstrap article content", source="library", limit=5)
        assert any("002-test-art.md" in r.path for r in art_results), \
            "Article file must be findable under source='library' after sync"
    finally:
        idx.close()


def test_sync_knowledge_disables_index_on_failure(tmp_path: Path) -> None:
    """sync_knowledge() sets knowledge_index=None when sync raises — grep fallback must activate for the session."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory_file(memory_dir / "001-test-mem.md", mem_id=1, body="Should fail.")

    idx = KnowledgeIndex(config=CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_search_backend="fts5",
        knowledge_reranker_provider="none",
    ))
    idx.close()  # closed before sync so sync_dir raises

    deps = _make_deps(tmp_path, knowledge_index=idx, memory_dir=memory_dir)
    sync_knowledge(deps, TerminalFrontend())

    assert deps.services.knowledge_index is None, \
        "sync_knowledge must set knowledge_index=None after sync failure"


def test_resolve_knowledge_backend_degrades_hybrid_to_fts5_when_vec_setup_fails(tmp_path: Path) -> None:
    """Hybrid bootstrap must degrade to FTS5 when sqlite-vec setup fails."""
    config = CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_search_backend="hybrid",
        knowledge_embedding_provider="none",
        knowledge_embedding_dims=0,
        knowledge_reranker_provider="none",
    )

    resolved_config, knowledge_index, statuses = resolve_knowledge_backend(config)
    assert resolved_config.knowledge_search_backend == "fts5"
    assert knowledge_index is not None, "Bootstrap must keep FTS search available after hybrid failure"
    assert any("using fts5" in status for status in statuses), \
        "Degradation must surface an explicit startup status message"

    try:
        tables = {
            row[0]
            for row in knowledge_index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
            ).fetchall()
        }
        assert "docs_fts" in tables, "FTS tables must exist after hybrid bootstrap degrades to fts5"
        assert "docs_vec" not in tables, "Hybrid vec tables must not remain active after fallback to fts5"
    finally:
        knowledge_index.close()


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


def test_display_welcome_banner_reads_counts_from_deps(tmp_path: Path) -> None:
    """display_welcome_banner() must read tool/skill/MCP counts from deps.session — not from a RuntimeCheck."""
    deps = _make_deps(tmp_path, mcp_servers={})
    deps.session.tool_names = ["tool_a", "tool_b", "tool_c"]
    deps.session.skill_registry = [{"name": "skill_x"}, {"name": "skill_y"}]
    deps.session.slash_command_count = 2

    with console.capture() as cap:
        display_welcome_banner(deps, deps.config)
    output = cap.get()

    assert "Tools: 3" in output, "Banner must show tool count from deps.session.tool_names"
    assert "Skills: 2" in output, "Banner must show skill count from deps.session.skill_registry"
    assert "MCP: 0" in output, "Banner must show MCP count from deps.config.mcp_servers"
    assert "Commands:" in output, "Banner must show slash command count from BUILTIN_COMMANDS"
