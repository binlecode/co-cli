"""Functional tests for bootstrap-sequence behaviors (real components, direct API calls)."""

import os
from pathlib import Path

import yaml
from tests._settings import make_settings

from co_cli.bootstrap.core import (
    _discover_knowledge_backend,
    _resolve_reranker,
    _sync_knowledge_store,
    restore_session,
)
from co_cli.config._knowledge import LlmModelSettings
from co_cli.context.session import session_filename
from co_cli.context.types import SafetyState
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.display._core import TerminalFrontend
from co_cli.knowledge._store import KnowledgeStore
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(
    tmp_path: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
    memory_dir: Path | None = None,
    library_dir: Path | None = None,
    mcp_servers: dict | None = None,
) -> CoDeps:
    config = make_settings(
        mcp_servers=mcp_servers if mcp_servers is not None else {},
    )
    runtime = CoRuntimeState(safety_state=SafetyState())
    return CoDeps(
        shell=ShellBackend(),
        knowledge_store=knowledge_store,
        config=config,
        session=CoSessionState(),
        runtime=runtime,
        sessions_dir=tmp_path / "sessions",
        memory_dir=memory_dir or tmp_path / "memory",
        library_dir=library_dir or tmp_path / "library",
    )


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


def test_sync_knowledge_store_indexes_article_only(tmp_path: Path) -> None:
    """_sync_knowledge_store reconciles article files into the store; memory is not indexed."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    _write_memory_file(
        memory_dir / "001-test-mem.md",
        mem_id=1,
        body=(
            "Finch Weinberg is a robotics engineer who survived a solar flare "
            "that scorched the Earth. He lives in a bunker in St. Louis with his "
            "dog Goodyear and a robot named Jeff he built to take care of the dog."
        ),
    )
    _write_article_file(
        library_dir / "002-test-art.md",
        art_id=2,
        body=(
            "The movie Finch (2021) directed by Miguel Sapochnik stars Tom Hanks "
            "as the titular character. The film explores themes of companionship, "
            "trust, and what it means to be alive in a post-apocalyptic world. "
            "Jeff the robot learns to drive an RV across the American West."
        ),
    )

    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "fts5",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    assert store is not None, "_discover_knowledge_backend must return a store for fts5"
    try:
        store = _sync_knowledge_store(store, config, TerminalFrontend(), memory_dir, library_dir)
        assert store is not None, "_sync_knowledge_store must not disable the store on success"

        # Memory is no longer synced to FTS by bootstrap — only articles are indexed
        art_results = store.search("Tom Hanks post-apocalyptic robot", source="library", limit=5)
        assert any("002-test-art.md" in r.path for r in art_results), (
            "Article about the Finch movie must be findable via FTS5"
        )
    finally:
        if store is not None:
            store.close()


def test_discover_knowledge_backend_returns_none_on_grep(tmp_path: Path) -> None:
    """_discover_knowledge_backend returns None when backend is grep — no store needed."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(update={"search_backend": "grep"}),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    assert store is None, "_discover_knowledge_backend must return None store for grep backend"
    assert config.knowledge.search_backend == "grep"
    assert not degradations, "grep config must have no degradations"


def test_discover_knowledge_backend_fts5_no_degradation(tmp_path: Path) -> None:
    """FTS5 configured with embedding disabled → store constructed, no degradation recorded."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "fts5",
                "embedding_provider": "none",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    try:
        assert store is not None, "FTS5 must construct a store"
        assert config.knowledge.search_backend == "fts5"
        assert not degradations, "FTS5 happy path must have no degradations"
    finally:
        if store is not None:
            store.close()


def test_knowledge_store_direct_construction_hybrid_with_provider_none_uses_fts5(
    tmp_path: Path,
) -> None:
    """KnowledgeStore constructed directly with hybrid + provider=none must silently use fts5."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "hybrid",
                "embedding_provider": "none",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    try:
        assert store._backend == "fts5", (
            "KnowledgeStore must degrade to fts5 when embedding_provider is none"
        )
    finally:
        store.close()


def test_discover_knowledge_backend_degrades_hybrid_to_fts5_when_embedder_unavailable(
    tmp_path: Path,
) -> None:
    """Hybrid degrades to FTS5 when embedder unreachable — degraded store must sync and search."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    _write_article_file(
        library_dir / "001-test-degraded.md",
        art_id=1,
        body=(
            "Finch programmed Jeff with three directives: protect Goodyear, "
            "never hurt a living thing, and always tell the truth. These rules "
            "guide Jeff's behavior throughout the journey to San Francisco."
        ),
    )

    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "hybrid",
                "embedding_provider": "tei",
                "embed_api_url": "http://127.0.0.1:1/embed",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    try:
        assert store is not None, "Discovery must return a store (degraded to fts5), not None"
        assert config.knowledge.search_backend == "fts5", (
            "Discovery must degrade hybrid to fts5 when embedder is unavailable"
        )
        assert "knowledge" in degradations, (
            "Discovery must record degradation in degradations dict"
        )

        # Degraded FTS5 store must still sync and search via keyword matching (articles only)
        store = _sync_knowledge_store(store, config, TerminalFrontend(), memory_dir, library_dir)
        assert store is not None, "Sync must succeed on the degraded fts5 store"
        results = store.search("Jeff directives protect Goodyear", source="library", limit=5)
        assert any("001-test-degraded.md" in r.path for r in results), (
            "Degraded fts5 store must find Jeff's directives via keyword search on articles"
        )
    finally:
        if store is not None:
            store.close()


def test_sync_knowledge_store_failure_returns_none(tmp_path: Path) -> None:
    """_sync_knowledge_store must close the store and return None when sync raises."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    _write_article_file(
        library_dir / "001-test-art.md",
        art_id=1,
        body="Finch's bunker contained decades of canned food and a working power grid.",
    )
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "fts5",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    assert store is not None
    # Close the store to make sync_dir raise on the dead connection
    store.close()
    result = _sync_knowledge_store(store, config, TerminalFrontend(), memory_dir, library_dir)
    assert result is None, "_sync_knowledge_store must return None when sync fails"


def test_restore_session_existing_returns_path(tmp_path: Path) -> None:
    """restore_session() with an existing session must restore its path."""
    from datetime import UTC, datetime

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    created_at = datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC)
    name = session_filename(created_at, "aaaaaaaa-0000-0000-0000-000000000000")
    existing = sessions_dir / name
    existing.touch()

    deps = _make_deps(tmp_path)
    result = restore_session(deps, TerminalFrontend())

    assert result == existing, "restore_session() must return the existing session path"
    assert deps.session.session_path == existing, (
        "restore_session() must set deps.session.session_path to the existing path"
    )


def test_restore_session_empty_dir_creates_new_path(tmp_path: Path) -> None:
    """restore_session() with no sessions returns a new path without writing a file."""
    deps = _make_deps(tmp_path)
    result = restore_session(deps, TerminalFrontend())

    assert isinstance(result, Path), "restore_session() must return a Path"
    assert result.suffix == ".jsonl", "New session path must have .jsonl suffix"
    assert deps.session.session_path == result, "session_path must be set in deps"
    # File is NOT written until first append_transcript
    assert not result.exists(), "New session file must not exist until first append_transcript"


def test_restore_session_picks_most_recent(tmp_path: Path) -> None:
    """restore_session() must pick the most recent session by lexicographic filename sort."""
    from datetime import UTC, datetime

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    older = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC)
    old_path = sessions_dir / session_filename(older, "aaaaaaaa-0000-0000-0000-000000000000")
    new_path = sessions_dir / session_filename(newer, "bbbbbbbb-0000-0000-0000-000000000000")
    old_path.touch()
    new_path.touch()

    deps = _make_deps(tmp_path)
    result = restore_session(deps, TerminalFrontend())

    assert result == new_path, "restore_session() must pick the most recently dated session"


def test_resolve_reranker_nothing_configured_returns_unchanged() -> None:
    """No reranker configured → config unchanged, no status messages."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "cross_encoder_reranker_url": None,
                "llm_reranker": None,
            }
        ),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.cross_encoder_reranker_url is None
    assert config.knowledge.llm_reranker is None
    assert statuses == []


def test_resolve_reranker_tei_unavailable_nulls_url() -> None:
    """TEI cross-encoder at a dead port → URL nulled, degradation status emitted."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "cross_encoder_reranker_url": "http://127.0.0.1:19999",
                "llm_reranker": None,
            }
        ),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.cross_encoder_reranker_url is None
    assert any("cross-encoder" in s.lower() or "tei" in s.lower() for s in statuses)


def test_resolve_reranker_llm_unavailable_nulls_reranker() -> None:
    """LLM reranker with gemini provider but no API key → check_reranker_llm returns error → reranker nulled."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "cross_encoder_reranker_url": None,
                "llm_reranker": LlmModelSettings(provider="gemini", model="gemini-2.0-flash"),
            }
        ),
        llm=make_settings().llm.model_copy(update={"provider": "gemini", "api_key": None}),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.llm_reranker is None
    assert any("llm" in s.lower() or "reranker" in s.lower() for s in statuses)


def test_resolve_reranker_llm_ollama_unreachable_degrades() -> None:
    """LLM reranker with Ollama provider but unreachable host → reranker nulled (warn != ok)."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "cross_encoder_reranker_url": None,
                "llm_reranker": LlmModelSettings(provider="ollama-openai", model="reranker-model"),
            }
        ),
        llm=make_settings().llm.model_copy(
            update={"provider": "ollama-openai", "host": "http://localhost:1"}
        ),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.llm_reranker is None
    assert any("llm" in s.lower() or "reranker" in s.lower() for s in statuses)


def test_resolve_reranker_both_unavailable_degrades_independently() -> None:
    """TEI dead + LLM reranker with no API key → both nulled, two separate status messages."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "cross_encoder_reranker_url": "http://127.0.0.1:19999",
                "llm_reranker": LlmModelSettings(provider="gemini", model="gemini-2.0-flash"),
            }
        ),
        llm=make_settings().llm.model_copy(update={"provider": "gemini", "api_key": None}),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.cross_encoder_reranker_url is None
    assert config.knowledge.llm_reranker is None
    assert len(statuses) == 2


def test_skill_loading_project_skill_registered(tmp_path: Path) -> None:
    """Project skill directory with one valid skill: skill appears in loaded commands."""
    from co_cli.commands._commands import _load_skills, get_skill_registry
    from co_cli.config._core import settings

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


def test_restore_session_stale_json_no_jsonl_creates_new_session(tmp_path: Path) -> None:
    """restore_session() with only stale .json files (no paired .jsonl) creates a new session."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    # Stale JSON sidecar with no paired .jsonl — find_latest_session finds nothing
    (sessions_dir / "bad-session.json").write_text("not valid json{{{", encoding="utf-8")

    deps = _make_deps(tmp_path)
    result = restore_session(deps, TerminalFrontend())

    assert isinstance(result, Path), "restore_session() must return a Path"
    assert deps.session.session_path == result


def test_restore_session_readonly_dir_does_not_raise(tmp_path: Path) -> None:
    """restore_session() must not raise when sessions_dir is read-only (no write on session create)."""
    readonly_dir = tmp_path / "readonly_sessions"
    readonly_dir.mkdir()
    os.chmod(readonly_dir, 0o555)
    try:
        config = make_settings(mcp_servers={})
        runtime = CoRuntimeState(safety_state=SafetyState())
        deps = CoDeps(
            shell=ShellBackend(),
            knowledge_store=None,
            config=config,
            session=CoSessionState(),
            runtime=runtime,
            sessions_dir=readonly_dir,
            memory_dir=tmp_path / "memory",
            library_dir=tmp_path / "library",
        )
        result = restore_session(deps, TerminalFrontend())
        assert isinstance(result, Path), "restore_session() must return a Path"
        assert deps.session.session_path == result
    finally:
        os.chmod(readonly_dir, 0o755)
