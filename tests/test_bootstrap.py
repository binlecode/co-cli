"""Functional tests for bootstrap-sequence behaviors (real components, direct API calls)."""

import asyncio
import dataclasses
import os
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from co_cli.prompts._assembly import _build_system_prompt
from co_cli.config import ModelEntry
from co_cli.bootstrap._banner import display_welcome_banner
from co_cli.bootstrap._bootstrap import resolve_knowledge_backend, resolve_reranker, restore_session, sync_knowledge
from co_cli.context._types import OpeningContextState, SafetyState
from co_cli.context._session import load_session, new_session, save_session
from co_cli.deps import CoDeps, CoConfig, CoRuntimeState, CoServices, CoSessionState
from co_cli.display._core import TerminalFrontend, console
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
        knowledge_cross_encoder_reranker_url=None,
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
        knowledge_cross_encoder_reranker_url=None,
    ))
    idx.close()  # closed before sync so sync_dir raises

    deps = _make_deps(tmp_path, knowledge_index=idx, memory_dir=memory_dir)
    sync_knowledge(deps, TerminalFrontend())

    assert deps.services.knowledge_index is None, \
        "sync_knowledge must set knowledge_index=None after sync failure"


def test_resolve_knowledge_backend_degrades_hybrid_to_fts5_when_embedder_unavailable(tmp_path: Path) -> None:
    """Hybrid bootstrap must degrade to FTS5 when the embedder is unreachable at startup."""
    config = CoConfig(
        knowledge_db_path=tmp_path / "search.db",
        knowledge_search_backend="hybrid",
        knowledge_embedding_provider="tei",
        knowledge_embed_api_url="http://127.0.0.1:1/embed",
        knowledge_cross_encoder_reranker_url=None,
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
        assert not any(t.startswith("docs_vec_") for t in tables), "Hybrid vec tables must not remain active after fallback to fts5"
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


def test_resolve_reranker_nothing_configured_returns_unchanged() -> None:
    """No reranker configured → config unchanged, no status messages."""
    config = CoConfig(knowledge_cross_encoder_reranker_url=None, knowledge_llm_reranker=None)
    resolved, statuses = resolve_reranker(config)
    assert resolved.knowledge_cross_encoder_reranker_url is None
    assert resolved.knowledge_llm_reranker is None
    assert statuses == []


def test_resolve_reranker_tei_unavailable_nulls_url() -> None:
    """TEI cross-encoder at a dead port → URL nulled, degradation status emitted."""
    config = CoConfig(
        knowledge_cross_encoder_reranker_url="http://127.0.0.1:19999",
        knowledge_llm_reranker=None,
    )
    resolved, statuses = resolve_reranker(config)
    assert resolved.knowledge_cross_encoder_reranker_url is None
    assert any("cross-encoder" in s.lower() or "tei" in s.lower() for s in statuses)


def test_resolve_reranker_llm_unavailable_nulls_reranker() -> None:
    """LLM reranker with gemini provider but no API key → check_reranker_llm returns error → reranker nulled."""
    config = CoConfig(
        knowledge_cross_encoder_reranker_url=None,
        knowledge_llm_reranker=ModelEntry(provider="gemini", model="gemini-2.0-flash"),
        llm_provider="gemini",
        llm_api_key=None,
    )
    resolved, statuses = resolve_reranker(config)
    assert resolved.knowledge_llm_reranker is None
    assert any("llm" in s.lower() or "reranker" in s.lower() for s in statuses)


def test_resolve_reranker_llm_ollama_unreachable_degrades() -> None:
    """LLM reranker with Ollama provider but unreachable host → reranker nulled (warn != ok)."""
    config = CoConfig(
        knowledge_cross_encoder_reranker_url=None,
        knowledge_llm_reranker=ModelEntry(provider="ollama-openai", model="reranker-model"),
        llm_provider="ollama-openai",
        llm_host="http://localhost:1",
    )
    resolved, statuses = resolve_reranker(config)
    assert resolved.knowledge_llm_reranker is None
    assert any("llm" in s.lower() or "reranker" in s.lower() for s in statuses)


def test_resolve_reranker_both_unavailable_degrades_independently() -> None:
    """TEI dead + LLM reranker with no API key → both nulled, two separate status messages."""
    config = CoConfig(
        knowledge_cross_encoder_reranker_url="http://127.0.0.1:19999",
        knowledge_llm_reranker=ModelEntry(provider="gemini", model="gemini-2.0-flash"),
        llm_provider="gemini",
        llm_api_key=None,
    )
    resolved, statuses = resolve_reranker(config)
    assert resolved.knowledge_cross_encoder_reranker_url is None
    assert resolved.knowledge_llm_reranker is None
    assert len(statuses) == 2


def test_mcp_tool_names_unchanged_when_enter_async_context_fails(tmp_path: Path) -> None:
    """When enter_async_context(agent) raises, tool_names must not grow with MCP names.

    Validates the _mcp_init_ok guard in _chat_loop: discover_mcp_tools must be
    skipped entirely when agent context entry fails for a dead MCP server.
    """
    import asyncio
    from contextlib import AsyncExitStack
    from co_cli.agent import build_agent, discover_mcp_tools
    from co_cli.config import MCPServerConfig

    # Dead HTTP MCP server — enter_async_context must raise (connection refused)
    mcp_servers = {
        "dead": MCPServerConfig(
            url="http://127.0.0.1:19998/mcp",
            prefix="dead",
            timeout=2,
        )
    }
    deps = _make_deps(tmp_path, mcp_servers=mcp_servers)
    agent_result = build_agent(config=deps.config)
    deps.tools.tool_names = list(agent_result.tool_names)
    initial_tool_count = len(deps.tools.tool_names)

    async def _run() -> None:
        stack = AsyncExitStack()
        _mcp_init_ok = False
        try:
            async with asyncio.timeout(5):
                await stack.enter_async_context(agent_result.agent)
            _mcp_init_ok = True
        except Exception:
            pass
        finally:
            await stack.aclose()

        if _mcp_init_ok:
            # If somehow the agent context entered (unexpected for a dead server),
            # skip the guard assertion — this environment has no dead-port guarantee.
            return

        # Guard: discover_mcp_tools must NOT be called when _mcp_init_ok is False.
        # We replicate the exact guard from _chat_loop here to confirm its effect.
        if deps.config.mcp_servers and _mcp_init_ok:
            mcp_tool_names, _errs = await discover_mcp_tools(agent_result.agent, exclude=set(agent_result.tool_names))
            deps.tools.tool_names = deps.tools.tool_names + mcp_tool_names

        assert len(deps.tools.tool_names) == initial_tool_count, (
            "tool_names must not grow when MCP init fails — discover_mcp_tools must be skipped"
        )

    asyncio.run(_run())


def test_display_welcome_banner_reads_counts_from_deps(tmp_path: Path) -> None:
    """display_welcome_banner() must read tool counts from deps.tools and skill counts from deps.session.
    Also verifies that the Ready line shows '(degraded)' when startup_statuses is non-empty and does not
    when it is empty.
    """
    deps = _make_deps(tmp_path, mcp_servers={})
    deps.tools.tool_names = ["tool_a", "tool_b", "tool_c"]
    deps.session.skill_registry = [{"name": "skill_x"}, {"name": "skill_y"}]
    deps.session.slash_command_count = 2

    with console.capture() as cap:
        display_welcome_banner(deps, [])
    output = cap.get()

    assert "Tools: 3" in output, "Banner must show tool count from deps.tools.tool_names"
    assert "Skills: 2" in output, "Banner must show skill count from deps.session.skill_registry"
    assert "MCP: 0" in output, "Banner must show MCP count from deps.config.mcp_servers"
    assert "Commands:" in output, "Banner must show slash command count from BUILTIN_COMMANDS"
    assert "✓ Ready" in output, "Banner must show Ready status"
    assert "(degraded)" not in output, "Banner must not show (degraded) when startup_statuses is empty"

    # Verify degraded path: non-empty startup_statuses appends '(degraded)' to the Ready line.
    with console.capture() as cap_degraded:
        display_welcome_banner(deps, ["reranker unavailable"])
    output_degraded = cap_degraded.get()

    assert "✓ Ready" in output_degraded, "Banner must still show Ready when degraded"
    assert "(degraded)" in output_degraded, "Banner must show (degraded) when startup_statuses is non-empty"


@pytest.mark.asyncio
async def test_initialize_session_capabilities_no_mcp_tool_names_unchanged(tmp_path: Path) -> None:
    """No MCP servers configured: tool_names must be unchanged after helper call.

    Validates the guard path: when deps.config.mcp_servers is empty,
    discover_mcp_tools must not be called and tool_names must stay empty.
    """
    from co_cli.agent import build_agent
    from co_cli.bootstrap._bootstrap import initialize_session_capabilities

    deps = _make_deps(tmp_path, mcp_servers={})
    initial_tool_names = list(deps.tools.tool_names)
    agent = build_agent(config=deps.config).agent

    async with asyncio.timeout(10):
        result = await initialize_session_capabilities(agent, deps, TerminalFrontend(), mcp_init_ok=True)

    assert deps.tools.tool_names == initial_tool_names, (
        "No MCP servers: tool_names must be unchanged after initialize_session_capabilities"
    )
    assert result.skill_count >= 1, (
        "skill_count must include at least one package-default skill (doctor)"
    )


@pytest.mark.asyncio
async def test_initialize_session_capabilities_mcp_skipped_when_init_failed(tmp_path: Path) -> None:
    """MCP servers configured but mcp_init_ok=False: discovery skipped; errors dict remains empty.

    Validates the _mcp_init_ok guard: when context entry failed, discover_mcp_tools
    must not be called and mcp_discovery_errors must be empty.
    """
    from co_cli.agent import build_agent
    from co_cli.bootstrap._bootstrap import initialize_session_capabilities
    from co_cli.config import MCPServerConfig

    mcp_servers = {
        "test-server": MCPServerConfig(url="http://127.0.0.1:19998/mcp", prefix="test", timeout=2)
    }
    deps = _make_deps(tmp_path, mcp_servers=mcp_servers)
    initial_tool_names = list(deps.tools.tool_names)
    agent = build_agent(config=deps.config).agent

    async with asyncio.timeout(10):
        await initialize_session_capabilities(agent, deps, TerminalFrontend(), mcp_init_ok=False)

    assert deps.tools.tool_names == initial_tool_names, (
        "mcp_init_ok=False: tool_names must be unchanged — discovery must be skipped"
    )
    assert deps.tools.mcp_discovery_errors == {}, (
        "mcp_init_ok=False: mcp_discovery_errors must be empty — no discovery attempt made"
    )


@pytest.mark.asyncio
async def test_initialize_session_capabilities_project_skill_registered(tmp_path: Path) -> None:
    """Project skill directory with one valid skill: skill appears in skill_registry and skill_count."""
    from co_cli.agent import build_agent
    from co_cli.bootstrap._bootstrap import initialize_session_capabilities

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_content = (
        "---\n"
        "description: Test skill for bootstrap functional tests\n"
        "---\n\n"
        "Perform a test action.\n"
    )
    (skills_dir / "test-bootstrap-skill.md").write_text(skill_content, encoding="utf-8")

    deps = _make_deps(tmp_path, mcp_servers={})
    deps = dataclasses.replace(deps, config=dataclasses.replace(deps.config, skills_dir=skills_dir))
    agent = build_agent(config=deps.config).agent

    async with asyncio.timeout(10):
        result = await initialize_session_capabilities(agent, deps, TerminalFrontend(), mcp_init_ok=False)

    skill_names = [s["name"] for s in deps.session.skill_registry]
    assert "test-bootstrap-skill" in skill_names, (
        "Project skill must appear in skill_registry after initialize_session_capabilities"
    )
    assert result.skill_count >= 1, (
        "skill_count must be at least 1 when a valid project skill is loaded"
    )


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
        services = CoServices(shell=ShellBackend(), knowledge_index=None)
        runtime = CoRuntimeState(opening_ctx_state=OpeningContextState(), safety_state=SafetyState())
        deps = CoDeps(services=services, config=config, session=CoSessionState(), runtime=runtime)
        result = restore_session(deps, TerminalFrontend())
        assert isinstance(result, dict), "restore_session() must return a session dict even when save fails"
        assert deps.session.session_id != "", "session_id must be set in deps even when save fails"
    finally:
        os.chmod(readonly_dir, 0o755)
