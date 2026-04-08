"""Functional tests for bootstrap-sequence behaviors (real components, direct API calls)."""

import os
import yaml

from pathlib import Path

import pytest

from co_cli.config._llm import ModelConfig
from tests._settings import test_settings
from co_cli.bootstrap._bootstrap import _discover_knowledge_backend, _sync_knowledge_store, _resolve_reranker, restore_session
from co_cli.context.types import SafetyState
from co_cli.context.session import new_session, save_session, find_latest_session
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.display._core import TerminalFrontend
from co_cli.knowledge._store import KnowledgeStore
from co_cli.tools.shell_backend import ShellBackend
from tests._timeouts import HTTP_HEALTH_TIMEOUT_SECS


def _make_deps(
    tmp_path: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
    memory_dir: Path | None = None,
    library_dir: Path | None = None,
    mcp_servers: dict | None = None,
) -> CoDeps:
    config = test_settings(
        mcp_servers=mcp_servers if mcp_servers is not None else {},
    )
    runtime = CoRuntimeState(safety_state=SafetyState())
    return CoDeps(
        shell=ShellBackend(), knowledge_store=knowledge_store, config=config,
        session=CoSessionState(), runtime=runtime,
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



def test_sync_knowledge_store_indexes_memory_and_article(tmp_path: Path) -> None:
    """_sync_knowledge_store reconciles memory and article files into the store."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    _write_memory_file(
        memory_dir / "001-test-mem.md", mem_id=1,
        body=(
            "Finch Weinberg is a robotics engineer who survived a solar flare "
            "that scorched the Earth. He lives in a bunker in St. Louis with his "
            "dog Goodyear and a robot named Jeff he built to take care of the dog."
        ),
    )
    _write_article_file(
        library_dir / "002-test-art.md", art_id=2,
        body=(
            "The movie Finch (2021) directed by Miguel Sapochnik stars Tom Hanks "
            "as the titular character. The film explores themes of companionship, "
            "trust, and what it means to be alive in a post-apocalyptic world. "
            "Jeff the robot learns to drive an RV across the American West."
        ),
    )

    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "search_backend": "fts5",
            "cross_encoder_reranker_url": None,
        }),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    assert store is not None, "_discover_knowledge_backend must return a store for fts5"
    try:
        store = _sync_knowledge_store(store, config, TerminalFrontend(), memory_dir, library_dir)
        assert store is not None, "_sync_knowledge_store must not disable the store on success"

        mem_results = store.search("robotics engineer solar flare", source="memory", limit=5)
        assert any("001-test-mem.md" in r.path for r in mem_results), \
            "Memory about Finch the engineer must be findable via FTS5"

        art_results = store.search("Tom Hanks post-apocalyptic robot", source="library", limit=5)
        assert any("002-test-art.md" in r.path for r in art_results), \
            "Article about the Finch movie must be findable via FTS5"
    finally:
        if store is not None:
            store.close()


def _tei_embedder_available() -> bool:
    """Check if TEI embedder is reachable on the default port."""
    import urllib.request
    import json
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8283/embed",
            data=json.dumps({"inputs": "test"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=HTTP_HEALTH_TIMEOUT_SECS)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _tei_embedder_available(), reason="TEI embedder not running on 127.0.0.1:8283")
def test_discover_hybrid_happy_path_real_embedder(tmp_path: Path) -> None:
    """Full hybrid path: real TEI embedder → store, sync, vector+FTS5 search, reranking."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    _write_memory_file(
        memory_dir / "001-test-hybrid.md", mem_id=1,
        body=(
            "Finch built a robot named Jeff from salvaged parts inside his bunker. "
            "Jeff's neural network learns by observing Finch and Goodyear the dog. "
            "The robot must understand loyalty and caregiving before Finch dies."
        ),
    )
    _write_memory_file(
        memory_dir / "002-test-hybrid.md", mem_id=2,
        body=(
            "The UV index after the solar flare makes daytime surface travel lethal. "
            "Finch's RV has reinforced UV shielding. The journey from St. Louis to "
            "San Francisco tests Jeff's ability to navigate and make decisions alone."
        ),
    )
    _write_article_file(
        library_dir / "003-test-hybrid.md", art_id=3,
        body=(
            "In the film Finch, the relationship between Jeff the robot and "
            "Goodyear the dog mirrors the bond between Finch and Goodyear. "
            "Jeff initially frightens the dog but gradually earns trust through "
            "patience and gentle behavior during the cross-country road trip."
        ),
    )

    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={"search_backend": "hybrid"}),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    try:
        assert store is not None, "Hybrid with real embedder must construct a store"
        assert config.knowledge.search_backend == "hybrid", \
            "Backend must stay hybrid when embedder is available"
        assert not degradations, "No degradation when hybrid succeeds"

        store = _sync_knowledge_store(store, config, TerminalFrontend(), memory_dir, library_dir)
        assert store is not None, "Sync must succeed on hybrid store"

        # Semantic search — "robot learning to care for animals" should find the
        # Jeff/Goodyear memories via embedding similarity, not just keyword match
        results = store.search("robot learning to care for animals", source="memory", limit=5)
        assert len(results) >= 1, "Hybrid search must return results for semantic query"
        assert any("001-test-hybrid.md" in r.path for r in results), \
            "Semantic search must find the Jeff caregiving memory"

        # Cross-source search — article about Jeff and Goodyear's bond
        art_results = store.search("robot dog trust road trip", source="library", limit=5)
        assert any("003-test-hybrid.md" in r.path for r in art_results), \
            "Hybrid search must find the article about Jeff and Goodyear"

        # Keyword-specific search — "UV index" is only in the second memory
        uv_results = store.search("UV index solar flare shielding", source="memory", limit=5)
        assert any("002-test-hybrid.md" in r.path for r in uv_results), \
            "Search must find the UV/travel memory"
    finally:
        if store is not None:
            store.close()


def test_discover_knowledge_backend_returns_none_on_grep(tmp_path: Path) -> None:
    """_discover_knowledge_backend returns None when backend is grep — no store needed."""
    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={"search_backend": "grep"}),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    assert store is None, "_discover_knowledge_backend must return None store for grep backend"
    assert config.knowledge.search_backend == "grep"
    assert not degradations, "grep config must have no degradations"


def test_discover_knowledge_backend_fts5_no_degradation(tmp_path: Path) -> None:
    """FTS5 configured with embedding disabled → store constructed, no degradation recorded."""
    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "search_backend": "fts5",
            "embedding_provider": "none",
            "cross_encoder_reranker_url": None,
        }),
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


def test_discover_knowledge_backend_degrades_hybrid_to_fts5_when_embedder_unavailable(tmp_path: Path) -> None:
    """Hybrid degrades to FTS5 when embedder unreachable — degraded store must sync and search."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory_file(
        memory_dir / "001-test-degraded.md", mem_id=1,
        body=(
            "Finch programmed Jeff with three directives: protect Goodyear, "
            "never hurt a living thing, and always tell the truth. These rules "
            "guide Jeff's behavior throughout the journey to San Francisco."
        ),
    )

    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "search_backend": "hybrid",
            "embedding_provider": "tei",
            "embed_api_url": "http://127.0.0.1:1/embed",
            "cross_encoder_reranker_url": None,
        }),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    try:
        assert store is not None, "Discovery must return a store (degraded to fts5), not None"
        assert config.knowledge.search_backend == "fts5", \
            "Discovery must degrade hybrid to fts5 when embedder is unavailable"
        assert "knowledge" in degradations, \
            "Discovery must record degradation in degradations dict"

        # Degraded FTS5 store must still sync and search via keyword matching
        store = _sync_knowledge_store(store, config, TerminalFrontend(), memory_dir, tmp_path / "library")
        assert store is not None, "Sync must succeed on the degraded fts5 store"
        results = store.search("Jeff directives protect Goodyear", source="memory", limit=5)
        assert any("001-test-degraded.md" in r.path for r in results), \
            "Degraded fts5 store must find Jeff's directives via keyword search"
    finally:
        if store is not None:
            store.close()


def test_sync_knowledge_store_failure_returns_none(tmp_path: Path) -> None:
    """_sync_knowledge_store must close the store and return None when sync raises."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory_file(
        memory_dir / "001-test-mem.md", mem_id=1,
        body="Finch's bunker contained decades of canned food and a working power grid.",
    )
    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "search_backend": "fts5",
            "cross_encoder_reranker_url": None,
        }),
    )
    degradations: dict[str, str] = {}
    store = _discover_knowledge_backend(config, TerminalFrontend(), degradations)
    assert store is not None
    # Close the store to make sync_dir raise on the dead connection
    store.close()
    result = _sync_knowledge_store(store, config, TerminalFrontend(), memory_dir, tmp_path / "library")
    assert result is None, "_sync_knowledge_store must return None when sync fails"


def test_restore_session_existing_returns_same_id(tmp_path: Path) -> None:
    """restore_session() with an existing session in sessions/ must restore the same session_id."""
    sessions_dir = tmp_path / "sessions"
    session_data = new_session()
    save_session(sessions_dir, session_data)
    original_id = session_data["session_id"]

    deps = _make_deps(tmp_path)
    restore_session(deps, TerminalFrontend())

    assert deps.session.session_id == original_id, \
        "restore_session() must restore the on-disk session_id"


def test_restore_session_empty_dir_creates_new_id(tmp_path: Path) -> None:
    """restore_session() with no sessions creates and persists a new session_id."""
    deps = _make_deps(tmp_path)
    restore_session(deps, TerminalFrontend())

    assert deps.session.session_id != ""
    # Verify standard UUID format (with dashes)
    assert "-" in deps.session.session_id, \
        "Session ID must use standard UUID format with dashes"
    sessions_dir = tmp_path / "sessions"
    on_disk = find_latest_session(sessions_dir)
    assert on_disk is not None
    assert on_disk["session_id"] == deps.session.session_id, \
        "restore_session() must persist the new session_id to disk"


def test_restore_session_picks_most_recent(tmp_path: Path) -> None:
    """restore_session() must pick the most recently modified session file."""
    import time
    sessions_dir = tmp_path / "sessions"
    old = new_session()
    save_session(sessions_dir, old)
    time.sleep(0.05)
    recent = new_session()
    save_session(sessions_dir, recent)

    deps = _make_deps(tmp_path)
    restore_session(deps, TerminalFrontend())

    assert deps.session.session_id == recent["session_id"], \
        "restore_session() must pick the most recently modified session"


def test_resolve_reranker_nothing_configured_returns_unchanged() -> None:
    """No reranker configured → config unchanged, no status messages."""
    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "cross_encoder_reranker_url": None,
            "llm_reranker": None,
        }),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.cross_encoder_reranker_url is None
    assert config.knowledge.llm_reranker is None
    assert statuses == []


def test_resolve_reranker_tei_unavailable_nulls_url() -> None:
    """TEI cross-encoder at a dead port → URL nulled, degradation status emitted."""
    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "cross_encoder_reranker_url": "http://127.0.0.1:19999",
            "llm_reranker": None,
        }),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.cross_encoder_reranker_url is None
    assert any("cross-encoder" in s.lower() or "tei" in s.lower() for s in statuses)


def test_resolve_reranker_llm_unavailable_nulls_reranker() -> None:
    """LLM reranker with gemini provider but no API key → check_reranker_llm returns error → reranker nulled."""
    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "cross_encoder_reranker_url": None,
            "llm_reranker": ModelConfig(provider="gemini", model="gemini-2.0-flash"),
        }),
        llm=test_settings().llm.model_copy(update={"provider": "gemini", "api_key": None}),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.llm_reranker is None
    assert any("llm" in s.lower() or "reranker" in s.lower() for s in statuses)


def test_resolve_reranker_llm_ollama_unreachable_degrades() -> None:
    """LLM reranker with Ollama provider but unreachable host → reranker nulled (warn != ok)."""
    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "cross_encoder_reranker_url": None,
            "llm_reranker": ModelConfig(provider="ollama-openai", model="reranker-model"),
        }),
        llm=test_settings().llm.model_copy(update={"provider": "ollama-openai", "host": "http://localhost:1"}),
    )
    statuses: list[str] = []
    _resolve_reranker(config, statuses)
    assert config.knowledge.llm_reranker is None
    assert any("llm" in s.lower() or "reranker" in s.lower() for s in statuses)


def test_resolve_reranker_both_unavailable_degrades_independently() -> None:
    """TEI dead + LLM reranker with no API key → both nulled, two separate status messages."""
    config = test_settings(
        knowledge=test_settings().knowledge.model_copy(update={
            "cross_encoder_reranker_url": "http://127.0.0.1:19999",
            "llm_reranker": ModelConfig(provider="gemini", model="gemini-2.0-flash"),
        }),
        llm=test_settings().llm.model_copy(update={"provider": "gemini", "api_key": None}),
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


def test_restore_session_corrupt_json_creates_new_session(tmp_path: Path) -> None:
    """restore_session() with corrupt session files creates a new session instead of crashing."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "bad-session.json").write_text("not valid json{{{", encoding="utf-8")

    deps = _make_deps(tmp_path)
    result = restore_session(deps, TerminalFrontend())

    assert isinstance(result, dict), "restore_session() must return a session dict even with corrupt file"
    assert deps.session.session_id != "", "session_id must be set after corrupt file recovery"


def test_restore_session_oserror_on_save_does_not_raise(tmp_path: Path) -> None:
    """restore_session() must not raise when save_session() fails due to a permissions error."""
    readonly_dir = tmp_path / "readonly_sessions"
    readonly_dir.mkdir()
    os.chmod(readonly_dir, 0o555)
    try:
        config = test_settings(mcp_servers={})
        runtime = CoRuntimeState(safety_state=SafetyState())
        deps = CoDeps(
            shell=ShellBackend(), knowledge_store=None, config=config,
            session=CoSessionState(), runtime=runtime,
            sessions_dir=readonly_dir,
            memory_dir=tmp_path / "memory",
            library_dir=tmp_path / "library",
        )
        result = restore_session(deps, TerminalFrontend())
        assert isinstance(result, dict), "restore_session() must return a session dict even when save fails"
        assert deps.session.session_id != "", "session_id must be set in deps even when save fails"
    finally:
        os.chmod(readonly_dir, 0o755)
