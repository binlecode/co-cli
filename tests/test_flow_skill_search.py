"""skill_search tool — ranked discovery over the SkillIndex."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.agent.core import build_tool_registry
from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.memory_store import MemoryStore
from co_cli.skills.index import SkillIndex
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.skills import skill_manage, skill_search

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

# Use pure FTS5 (no reranker) for deterministic scoring — the production
# TEI reranker would otherwise float low-confidence rows above the zero threshold.
_FTS5_CONFIG = SETTINGS.knowledge.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_TEST_SETTINGS = SETTINGS.model_copy(update={"knowledge": _FTS5_CONFIG})


def _make_deps(tmp_path: Path) -> CoDeps:
    memory_db = tmp_path / "search.db"
    store = MemoryStore(config=_TEST_SETTINGS, memory_db_path=memory_db)
    skill_index = SkillIndex(config=_TEST_SETTINGS, memory_db_path=memory_db)
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, _TEST_SETTINGS, user_skills_dir=tmp_path)
    tool_registry = build_tool_registry(_TEST_SETTINGS)
    deps = CoDeps(
        shell=ShellBackend(),
        config=_TEST_SETTINGS,
        tool_index=dict(tool_registry.tool_index),
        session=CoSessionState(),
        skill_commands=skill_commands,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
        memory_store=store,
        skill_index=skill_index,
    )
    for name, skill in skill_commands.items():
        user_path = tmp_path / f"{name}.md"
        skill_path = (
            str(user_path) if user_path.is_file() else str(_BUNDLED_SKILLS_DIR / f"{name}.md")
        )
        skill_index.upsert(name, skill.description, skill_path)
    return deps


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _is_error(result) -> bool:
    return result.metadata is not None and result.metadata.get("error") is True


@pytest.mark.asyncio
async def test_skill_search_returns_ranked_hits(tmp_path: Path) -> None:
    """skill_search('diagnose') returns hits with {name, description, score, path}.

    Failure mode: if the SkillIndex is not populated or skill_search bypasses it,
    the model cannot discover bundled skills by keyword and falls back to guessing.
    """
    deps = _make_deps(tmp_path)
    try:
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await skill_search(_ctx(deps), query="diagnose")

        results = result.metadata.get("results", [])
        assert results, f"expected at least one hit for 'diagnose', got: {results}"
        assert any(r["name"] == "doctor" for r in results), (
            f"Expected 'doctor' skill to be findable by 'diagnose'; got: {[r['name'] for r in results]}"
        )
        for r in results:
            assert set(r.keys()) >= {"name", "description", "score", "path"}, (
                f"hit missing required fields: {r}"
            )
    finally:
        deps.memory_store.close()
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_search_no_match_returns_empty(tmp_path: Path) -> None:
    """A nonsense query returns count=0, results=[], not a tool_error."""
    deps = _make_deps(tmp_path)
    try:
        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await skill_search(
                _ctx(deps), query="qqzzxxyyqqzzxxyy_no_skill_can_match_this"
            )

        assert not _is_error(result)
        assert result.metadata.get("count") == 0
        assert result.metadata.get("results") == []
    finally:
        deps.memory_store.close()
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_search_empty_query_returns_tool_error(tmp_path: Path) -> None:
    """Empty / whitespace query is rejected with tool_error — browse via the prompt manifest instead."""
    deps = _make_deps(tmp_path)
    try:
        result_empty = await skill_search(_ctx(deps), query="")
        result_ws = await skill_search(_ctx(deps), query="   ")
        assert _is_error(result_empty), f"empty query must error, got: {result_empty.return_value}"
        assert _is_error(result_ws), f"whitespace must error, got: {result_ws.return_value}"
    finally:
        deps.memory_store.close()
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_search_finds_newly_created_skill(tmp_path: Path) -> None:
    """skill_manage(action='create', ...) then skill_search finds the new skill.

    Failure mode: if the lifecycle hook does not push the new skill into the SkillIndex,
    skill_search cannot discover skills created in this session until the next reload.
    """
    deps = _make_deps(tmp_path)
    try:
        await skill_manage(
            _ctx(deps),
            action="create",
            name="newly-created-tool",
            content="---\ndescription: A unique_marker_alpha_beta helper for verifying create-then-search\n---\nBody.\n",
        )

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await skill_search(_ctx(deps), query="unique_marker_alpha_beta")

        results = result.metadata.get("results", [])
        assert any(r["name"] == "newly-created-tool" for r in results), (
            f"Newly created skill must be findable; got: {results}"
        )
    finally:
        deps.memory_store.close()
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_search_does_not_return_deleted_skill(tmp_path: Path) -> None:
    """After skill_manage(action='delete', ...) the skill no longer appears in skill_search."""
    deps = _make_deps(tmp_path)
    try:
        await skill_manage(
            _ctx(deps),
            action="create",
            name="to-be-removed",
            content="---\ndescription: marker_delta_gamma ephemeral skill for deletion test\n---\nBody.\n",
        )
        before = await skill_search(_ctx(deps), query="marker_delta_gamma")
        assert any(r["name"] == "to-be-removed" for r in before.metadata.get("results", [])), (
            "precondition: skill must be findable before delete"
        )

        await skill_manage(_ctx(deps), action="delete", name="to-be-removed")

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            after = await skill_search(_ctx(deps), query="marker_delta_gamma")

        results = after.metadata.get("results", [])
        assert not any(r["name"] == "to-be-removed" for r in results), (
            f"Deleted skill must be absent from skill_search; got: {results}"
        )
    finally:
        deps.memory_store.close()
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_search_finds_installed_skill(tmp_path: Path) -> None:
    """After skill_manage(action='install', ...) the installed skill is discoverable via skill_search."""
    skill_file = tmp_path / "local-installable.md"
    skill_file.write_text(
        "---\ndescription: marker_epsilon_zeta installed-from-local recall test\n---\nInstalled body.\n",
        encoding="utf-8",
    )

    install_dir = tmp_path / "install-target"
    install_dir.mkdir()
    memory_db = tmp_path / "install-search.db"
    store = MemoryStore(config=_TEST_SETTINGS, memory_db_path=memory_db)
    skill_index = SkillIndex(config=_TEST_SETTINGS, memory_db_path=memory_db)
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, _TEST_SETTINGS, user_skills_dir=install_dir)
    deps = CoDeps(
        shell=ShellBackend(),
        config=_TEST_SETTINGS,
        tool_index=dict(build_tool_registry(_TEST_SETTINGS).tool_index),
        session=CoSessionState(),
        skill_commands=skill_commands,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=install_dir,
        tool_results_dir=install_dir / "tool-results",
        memory_store=store,
        skill_index=skill_index,
    )
    for name, skill in skill_commands.items():
        skill_index.upsert(name, skill.description, str(_BUNDLED_SKILLS_DIR / f"{name}.md"))

    try:
        result = await skill_manage(_ctx(deps), action="install", source=str(skill_file))
        assert not _is_error(result), f"install failed: {result.return_value}"

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            search_result = await skill_search(_ctx(deps), query="marker_epsilon_zeta")

        results = search_result.metadata.get("results", [])
        assert any(r["name"] == "local-installable" for r in results), (
            f"installed skill must be findable; got: {results}"
        )
    finally:
        deps.memory_store.close()
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_search_caps_at_limit(tmp_path: Path) -> None:
    """skill_search(limit=2) returns at most 2 hits even when more match."""
    deps = _make_deps(tmp_path)
    try:
        # Inject several user skills with a shared marker so the limit is exercised.
        for i in range(4):
            await skill_manage(
                _ctx(deps),
                action="create",
                name=f"limit-test-{i}",
                content=(
                    f"---\ndescription: shared_limit_marker_omega entry {i}\n---\nBody {i}.\n"
                ),
            )

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await skill_search(_ctx(deps), query="shared_limit_marker_omega", limit=2)

        results = result.metadata.get("results", [])
        assert len(results) == 2, f"limit=2 must cap at 2 hits, got {len(results)}"
    finally:
        deps.memory_store.close()
        deps.skill_index.close()


@pytest.mark.asyncio
async def test_skill_search_populates_description(tmp_path: Path) -> None:
    """Description in skill_search hits is populated (regression guard).

    Failure mode: if the search path drops description, the model has only a name
    and cannot judge skill relevance before loading the body.
    """
    deps = _make_deps(tmp_path)
    try:
        await skill_manage(
            _ctx(deps),
            action="create",
            name="desc-regression-skill",
            content=(
                "---\ndescription: marker_phi_chi_psi description regression guard text\n---\nBody.\n"
            ),
        )

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await skill_search(_ctx(deps), query="marker_phi_chi_psi")

        results = result.metadata.get("results", [])
        hit = next((r for r in results if r["name"] == "desc-regression-skill"), None)
        assert hit is not None, f"missing expected hit; got: {results}"
        assert "regression guard" in hit["description"], (
            f"description must be populated, got: {hit['description']!r}"
        )
    finally:
        deps.memory_store.close()
        deps.skill_index.close()
