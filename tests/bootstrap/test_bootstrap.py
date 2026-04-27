"""Functional tests for bootstrap-sequence behaviors (real components, direct API calls)."""

import os
from pathlib import Path

import pytest
import yaml
from pydantic_ai.mcp import MCPServerStdio
from pydantic_ai.toolsets import DeferredLoadingToolset
from tests._settings import make_settings

from co_cli.agent._mcp import MCPToolsetEntry, discover_mcp_tools
from co_cli.bootstrap.core import init_memory_index, restore_session
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.display._core import TerminalFrontend
from co_cli.knowledge._store import KnowledgeStore
from co_cli.memory.session import session_filename
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(
    tmp_path: Path,
    *,
    knowledge_store: KnowledgeStore | None = None,
    knowledge_dir: Path | None = None,
    mcp_servers: dict | None = None,
) -> CoDeps:
    config = make_settings(
        mcp_servers=mcp_servers if mcp_servers is not None else {},
    )
    runtime = CoRuntimeState()
    return CoDeps(
        shell=ShellBackend(),
        knowledge_store=knowledge_store,
        config=config,
        session=CoSessionState(),
        runtime=runtime,
        sessions_dir=tmp_path / "sessions",
        knowledge_dir=knowledge_dir or tmp_path / "knowledge",
    )


def _write_knowledge_file(
    path: Path,
    *,
    artifact_id: int | str,
    artifact_kind: str,
    body: str,
    extra: dict | None = None,
) -> None:
    """Write a canonical kind=knowledge artifact file."""
    fm = {
        "id": str(artifact_id),
        "kind": "knowledge",
        "artifact_kind": artifact_kind,
        "created": "2026-01-01T00:00:00+00:00",
        "tags": ["test"],
    }
    if extra:
        fm.update(extra)
    path.write_text(
        f"---\n{yaml.dump(fm, default_flow_style=False)}---\n\n{body}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Knowledge store: sync and search
# ---------------------------------------------------------------------------


def test_sync_knowledge_store_indexes_unified_knowledge(tmp_path: Path) -> None:
    """KnowledgeStore.sync_dir indexes both memory- and article-kind files under source='knowledge'."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    _write_knowledge_file(
        knowledge_dir / "001-test-mem.md",
        artifact_id=1,
        artifact_kind="preference",
        body=(
            "Finch Weinberg is a robotics engineer who survived a solar flare "
            "that scorched the Earth. He lives in a bunker in St. Louis with his "
            "dog Goodyear and a robot named Jeff he built to take care of the dog."
        ),
    )
    _write_knowledge_file(
        knowledge_dir / "002-test-art.md",
        artifact_id=2,
        artifact_kind="article",
        body=(
            "The movie Finch (2021) directed by Miguel Sapochnik stars Tom Hanks "
            "as the titular character. The film explores themes of companionship, "
            "trust, and what it means to be alive in a post-apocalyptic world. "
            "Jeff the robot learns to drive an RV across the American West."
        ),
        extra={"decay_protected": True, "source_ref": "https://example.com/test"},
    )

    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "fts5",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    try:
        store.sync_dir("knowledge", knowledge_dir)

        art_results = store.search("Tom Hanks post-apocalyptic robot", source="knowledge", limit=5)
        assert any("002-test-art.md" in r.path for r in art_results), (
            "Article about the Finch movie must be findable via FTS5"
        )
        mem_results = store.search("solar flare Goodyear bunker", source="knowledge", limit=5)
        assert any("001-test-mem.md" in r.path for r in mem_results), (
            "Memory-kind file must also be findable under the unified knowledge source"
        )
    finally:
        store.close()


def test_knowledge_store_direct_construction_hybrid_with_provider_none_uses_fts5(
    tmp_path: Path,
) -> None:
    """KnowledgeStore constructed with hybrid + provider=none must silently fall back to fts5."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "hybrid",
                "embedding_provider": "none",
                "cross_encoder_reranker_url": None,
            }
        ),
    )
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    _write_knowledge_file(
        knowledge_dir / "001-test.md",
        artifact_id=1,
        artifact_kind="article",
        body="hello world test content about robots and machines",
    )
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    try:
        store.sync_dir("test", knowledge_dir)
        results = store.search("hello world robots", source="test", limit=5)
        assert any("001-test.md" in r.path for r in results), (
            "KnowledgeStore must fall back to fts5 when embedding_provider is none"
        )
    finally:
        store.close()


def test_knowledge_store_hybrid_with_no_embedder_falls_back_to_fts5_search(
    tmp_path: Path,
) -> None:
    """KnowledgeStore with hybrid + no embedder silently degrades to fts5 — sync and search must work."""
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    _write_knowledge_file(
        knowledge_dir / "001-test-degraded.md",
        artifact_id=1,
        artifact_kind="article",
        body=(
            "Finch programmed Jeff with three directives: protect Goodyear, "
            "never hurt a living thing, and always tell the truth. These rules "
            "guide Jeff's behavior throughout the journey to San Francisco."
        ),
        extra={"decay_protected": True, "source_ref": "https://example.com/finch"},
    )
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
        store.sync_dir("knowledge", knowledge_dir)
        results = store.search("Jeff directives protect Goodyear", source="knowledge", limit=5)
        assert any("001-test-degraded.md" in r.path for r in results), (
            "Degraded fts5 store must find Jeff's directives via keyword search on articles"
        )
    finally:
        store.close()


def test_knowledge_store_search_succeeds_with_unreachable_reranker(
    tmp_path: Path,
) -> None:
    """KnowledgeStore with an unreachable cross-encoder reranker must still return search results."""
    config = make_settings(
        knowledge=make_settings().knowledge.model_copy(
            update={
                "search_backend": "fts5",
                "embedding_provider": "none",
                "cross_encoder_reranker_url": "http://127.0.0.1:19999",
            }
        ),
    )
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    _write_knowledge_file(
        knowledge_dir / "001-reranker-test.md",
        artifact_id=1,
        artifact_kind="article",
        body="Reranker resilience test content about robots and AI systems",
    )
    store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")
    try:
        store.sync_dir("test", knowledge_dir)
        results = store.search("robots AI", source="test", limit=5)
        assert any("001-reranker-test.md" in r.path for r in results), (
            "Search must succeed and return results even when cross-encoder reranker is unreachable"
        )
    finally:
        store.close()


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


# ---------------------------------------------------------------------------
# Discovery and session bootstrap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_mcp_tools_records_tool_prefix_for_missing_binary() -> None:
    """Failed MCP discovery must report the failing tool_prefix for downstream diagnostics."""
    server = MCPServerStdio(
        "nonexistent-binary-xyz",
        args=[],
        tool_prefix="testprefix",
    )
    entry = MCPToolsetEntry(
        toolset=DeferredLoadingToolset(server),
        server=server,
        approval=False,
        prefix="testprefix",
        timeout=5.0,
    )

    _, errors, _ = await discover_mcp_tools([entry], exclude=set())

    assert errors, "errors dict must be non-empty when MCP server binary does not exist"
    assert "testprefix" in errors, "Failed MCP discovery must preserve the configured tool_prefix"


def test_skill_loading_project_skill_registered(tmp_path: Path) -> None:
    """Project skill directory with one valid skill: skill appears in loaded commands."""
    from co_cli.commands.skills import get_skill_registry
    from co_cli.config._core import settings
    from co_cli.skills.loader import load_skills

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_content = (
        "---\n"
        "description: Test skill for bootstrap functional tests\n"
        "---\n\n"
        "Perform a test action.\n"
    )
    (skills_dir / "test-bootstrap-skill.md").write_text(skill_content, encoding="utf-8")

    skill_commands = load_skills(skills_dir, settings=settings)

    assert "test-bootstrap-skill" in skill_commands, (
        "Project skill must appear in skill_commands after load_skills"
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
        runtime = CoRuntimeState()
        deps = CoDeps(
            shell=ShellBackend(),
            knowledge_store=None,
            config=config,
            session=CoSessionState(),
            runtime=runtime,
            sessions_dir=readonly_dir,
            knowledge_dir=tmp_path / "knowledge",
        )
        result = restore_session(deps, TerminalFrontend())
        assert isinstance(result, Path), "restore_session() must return a Path"
        assert deps.session.session_path == result
    finally:
        os.chmod(readonly_dir, 0o755)


def testinit_memory_index_indexes_past_sessions(tmp_path: Path) -> None:
    """init_memory_index opens the DB and syncs past sessions; deps.memory_index is set."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    from co_cli.memory.store import MemoryIndex
    from co_cli.memory.transcript import append_messages

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)

    # Write a past session with searchable content
    from datetime import UTC, datetime

    from co_cli.memory.session import session_filename

    past_name = session_filename(
        datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC),
        "past0001-0000-0000-0000-000000000000",
    )
    past_path = sessions_dir / past_name
    append_messages(
        past_path,
        [
            ModelRequest(
                parts=[UserPromptPart(content="Explain the Fibonacci sequence in Python")]
            ),
            ModelResponse(
                parts=[
                    TextPart(
                        content="Fibonacci is a sequence where each number is the sum of the two preceding ones."
                    )
                ]
            ),
        ],
    )

    # Current session path (excluded from sync)
    current_name = session_filename(
        datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC),
        "curr0001-0000-0000-0000-000000000000",
    )
    current_path = sessions_dir / current_name

    deps = _make_deps(tmp_path)

    init_memory_index(deps, current_path, TerminalFrontend())

    assert isinstance(deps.memory_index, MemoryIndex), (
        "deps.memory_index must be a MemoryIndex after init_memory_index"
    )
    results = deps.memory_index.search("Fibonacci sequence")
    assert len(results) >= 1, "Indexed past session must be searchable by keyword"
    assert results[0].session_id == "past0001", (
        f"Result must reference the past session, got {results[0].session_id!r}"
    )
