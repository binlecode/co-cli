"""Tests for the memory recall tools — session read and grep_recall paths."""

import asyncio

import pytest
from opentelemetry import trace as otel_trace
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS, SETTINGS_NO_MCP
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.artifact import KnowledgeArtifact
from co_cli.memory.memory_store import MemoryStore
from co_cli.memory.service import reindex, save_artifact
from co_cli.skills.skill_types import SkillConfig
from co_cli.tools.memory.read import grep_recall, memory_read_session_turn
from co_cli.tools.memory.recall import _list_artifacts, memory_search
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(tmp_path, sessions_dir=None):
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        sessions_dir=sessions_dir or tmp_path / "sessions",
    )


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


@pytest.mark.asyncio
async def test_memory_read_session_turn_targeted_glob_locates_correct_file(
    tmp_path,
) -> None:
    """Targeted glob must locate the session matching the given session_id and not confuse it.

    Failure mode: glob `f'*-{session_id}.jsonl'` wrong → every session lookup returns
    'Unknown session_id', breaking agents that try to read past session turns.

    Creates two JSONL session files with distinct 8-char IDs. Calls
    memory_read_session_turn with ID_A and verifies:
    - The call succeeds (no "Unknown session_id" error).
    - Metadata carries the correct session_id back to the caller.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    # Two files with distinct session IDs in the suffix
    id_a = "aaaaaaaa"
    id_b = "bbbbbbbb"
    (sessions_dir / f"2026-01-01-T120000Z-{id_a}.jsonl").touch()
    (sessions_dir / f"2026-01-01-T120000Z-{id_b}.jsonl").touch()

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await memory_read_session_turn(ctx, id_a, 1, 5)

    assert "Unknown session_id" not in result.return_value, (
        f"Targeted glob failed to locate session '{id_a}': {result.return_value!r}"
    )
    assert result.metadata.get("session_id") == id_a, (
        f"Expected session_id={id_a!r} in metadata, got {result.metadata!r}"
    )


@pytest.mark.asyncio
async def test_memory_read_session_turn_unknown_id_returns_error(tmp_path) -> None:
    """A session_id with no matching file must return a 'Unknown session_id' error.

    Failure mode: glob matches wrong file (e.g. prefix instead of suffix) → agent
    reads wrong session data silently.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "2026-01-01-T120000Z-aaaaaaaa.jsonl").touch()

    deps = _make_deps(tmp_path, sessions_dir=sessions_dir)
    ctx = _ctx(deps)

    async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
        result = await memory_read_session_turn(ctx, "cccccccc", 1, 5)

    assert "Unknown session_id" in result.return_value, (
        f"Expected 'Unknown session_id' error for missing ID, got: {result.return_value!r}"
    )


def test_grep_recall_title_only_match() -> None:
    """grep_recall must find an artifact whose title matches the query but body does not.

    Failure mode: content-only filter → title-matched artifacts invisible during FTS fallback.
    """
    created = "2026-01-01T00:00:00Z"
    artifacts = [
        KnowledgeArtifact(
            id="aaaa",
            path=None,
            artifact_kind="note",
            title="RRF scoring design",
            content="This artifact contains no mention of the query term.",
            created=created,
        ),
        KnowledgeArtifact(
            id="bbbb",
            path=None,
            artifact_kind="note",
            title="Unrelated title",
            content="Also no match here.",
            created=created,
        ),
    ]

    results = grep_recall(artifacts, "rrf scoring", max_results=10)

    assert len(results) == 1, f"Expected 1 result, got {len(results)}: {results}"
    assert results[0].title == "RRF scoring design"


def test_grep_recall_content_match_still_works() -> None:
    """grep_recall must still find artifacts matched by content when title does not match.

    Ensures the title-search addition does not regress existing content-match behavior.
    """
    created = "2026-01-01T00:00:00Z"
    artifacts = [
        KnowledgeArtifact(
            id="cccc",
            path=None,
            artifact_kind="note",
            title="Unrelated title",
            content="The body mentions pydantic-ai validation here.",
            created=created,
        ),
    ]

    results = grep_recall(artifacts, "pydantic-ai", max_results=10)

    assert len(results) == 1
    assert "pydantic-ai" in results[0].content


# ---------------------------------------------------------------------------
# _list_artifacts — index-backed path and disk-scan fallback (TASK-16)
# ---------------------------------------------------------------------------

_NOOP_SPAN = otel_trace.get_tracer("test").start_span("noop")


def _make_deps_with_store(tmp_path, store: MemoryStore) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        knowledge_dir=tmp_path / "knowledge",
        memory_store=store,
    )


def _ctx_for(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _seed_artifacts(knowledge_dir, store, count=3):
    """Save `count` artifacts to disk and index them; return results sorted by creation order."""
    results = []
    for i in range(count):
        r = save_artifact(
            knowledge_dir,
            content=f"content for artifact {i}",
            artifact_kind="note",
            title=f"artifact {i}",
        )
        reindex(
            store,
            r.path,
            r.content,
            r.markdown_content,
            r.frontmatter_dict,
            r.filename_stem,
            chunk_tokens=600,
            chunk_overlap_tokens=80,
        )
        results.append(r)
    return results


def test_list_artifacts_index_backed_returns_sorted_limited(tmp_path):
    """_list_artifacts must return index-backed dicts sorted by created DESC, respecting limit.

    Failure mode: without index delegation, every empty-query memory_list call reads all
    .md files from disk even when the FTS5 index is warm.
    """
    store = MemoryStore(config=SETTINGS, memory_db_path=tmp_path / "search.db")
    try:
        deps = _make_deps_with_store(tmp_path, store)
        ctx = _ctx_for(deps)
        _seed_artifacts(tmp_path / "knowledge", store, count=3)

        results = _list_artifacts(ctx, kinds=None, limit=2, span=_NOOP_SPAN)

        assert len(results) == 2, f"Expected 2 (limit), got {len(results)}"
        for r in results:
            assert r["channel"] == "artifacts"
            assert r["title"]
            assert "path" in r
        # created DESC: newer index entries appear first
        paths = [r["path"] for r in results]
        assert len(set(paths)) == 2, "duplicate paths returned"
    finally:
        store.close()


def test_list_artifacts_disk_scan_fallback_when_no_store(tmp_path):
    """_list_artifacts must fall back to disk scan when memory_store is None.

    Failure mode: if disk-scan fallback is removed, memory_list with no store
    (e.g. cold start without FTS5) returns empty instead of available artifacts.
    """
    knowledge_dir = tmp_path / "knowledge"
    # Seed artifacts on disk only — no store
    store_tmp = MemoryStore(config=SETTINGS, memory_db_path=tmp_path / "tmp.db")
    try:
        _seed_artifacts(knowledge_dir, store_tmp, count=3)
    finally:
        store_tmp.close()

    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        knowledge_dir=knowledge_dir,
        memory_store=None,
    )
    ctx = _ctx_for(deps)

    results = _list_artifacts(ctx, kinds=None, limit=10, span=_NOOP_SPAN)

    assert len(results) == 3, f"Expected 3 from disk scan, got {len(results)}"
    for r in results:
        assert r["channel"] == "knowledge"


# ---------------------------------------------------------------------------
# memory_search — channel rename + deprecated-channel error paths
# ---------------------------------------------------------------------------


def _make_deps_with_store_only(tmp_path, store: MemoryStore) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        knowledge_dir=tmp_path / "knowledge",
        memory_store=store,
    )


@pytest.mark.asyncio
async def test_memory_search_skills_channel_errors_with_skill_search_hint(tmp_path) -> None:
    """memory_search(channel='skills') returns a structured tool_error directing to skill_search.

    Failure mode: silent fallback to all-channels search lets the model believe skills
    were searched when the dedicated skill_search tool is the real surface.
    """
    store = MemoryStore(config=SETTINGS, memory_db_path=tmp_path / "search.db")
    try:
        deps = _make_deps_with_store_only(tmp_path, store)
        ctx = RunContext(deps=deps, model=None, usage=RunUsage())

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_search(ctx, query="test", channel="skills")

        assert result.metadata is not None, "tool_error must populate metadata"
        assert result.metadata.get("error") is True, (
            f"channel='skills' must return tool_error, got: {result.return_value!r}"
        )
        assert "skill_search" in result.return_value, (
            f"error message must direct to skill_search, got: {result.return_value!r}"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_search_canon_channel_errors_with_personality_hint(tmp_path) -> None:
    """memory_search(channel='canon') returns a structured tool_error directing to personality.

    Failure mode: returning canon hits would re-introduce the tier conflation the
    four-tier decomposition rejected — canon is doctrine, not memory.
    """
    store = MemoryStore(config=SETTINGS, memory_db_path=tmp_path / "search.db")
    try:
        deps = _make_deps_with_store_only(tmp_path, store)
        ctx = RunContext(deps=deps, model=None, usage=RunUsage())

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_search(ctx, query="test", channel="canon")

        assert result.metadata is not None, "tool_error must populate metadata"
        assert result.metadata.get("error") is True, (
            f"channel='canon' must return tool_error, got: {result.return_value!r}"
        )
        assert (
            "personality" in result.return_value.lower()
            or "doctrine" in result.return_value.lower()
        ), f"error must reference personality/doctrine, got: {result.return_value!r}"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_search_knowledge_channel_returns_knowledge_only(tmp_path) -> None:
    """memory_search(channel='knowledge') returns knowledge-channel hits with correct discriminator.

    Failure mode: a leak of session results into the knowledge channel would corrupt the
    flat-result discriminator the agent relies on to route follow-up reads.
    """
    store = MemoryStore(config=SETTINGS, memory_db_path=tmp_path / "search.db")
    try:
        knowledge_dir = tmp_path / "knowledge"
        r = save_artifact(
            knowledge_dir,
            content="artifact about widget_marker_test content",
            artifact_kind="note",
            title="widget notes",
        )
        reindex(
            store,
            r.path,
            r.content,
            r.markdown_content,
            r.frontmatter_dict,
            r.filename_stem,
            chunk_tokens=600,
            chunk_overlap_tokens=80,
        )

        deps = _make_deps_with_store_only(tmp_path, store)
        ctx = RunContext(deps=deps, model=None, usage=RunUsage())

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_search(ctx, query="widget_marker_test", channel="knowledge")

        results = result.metadata.get("results", [])
        assert results, f"expected at least one knowledge hit, got: {results}"
        assert all(r["channel"] == "knowledge" for r in results), (
            f"all results must carry channel='knowledge', got: {results}"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_memory_search_empty_query_browse_excludes_skills_section(tmp_path) -> None:
    """Empty-query browse returns sessions + knowledge only — no 'Available skills:' section.

    Failure mode: a stale skills branch in the browse path would mix tiers and contradict
    the new prompt-injection-based skill discovery.
    """
    store = MemoryStore(config=SETTINGS, memory_db_path=tmp_path / "search.db")
    try:
        deps = CoDeps(
            shell=ShellBackend(),
            config=SETTINGS,
            session=CoSessionState(),
            knowledge_dir=tmp_path / "knowledge",
            memory_store=store,
            skill_commands={
                "my-skill": SkillConfig(name="my-skill", description="does something useful")
            },
        )
        ctx = RunContext(deps=deps, model=None, usage=RunUsage())

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await memory_search(ctx, query="")

        assert "Available skills:" not in result.return_value, (
            f"empty-query browse must NOT include skills section, got: {result.return_value!r}"
        )
        assert "my-skill" not in result.return_value, (
            f"empty-query browse must NOT enumerate skill names, got: {result.return_value!r}"
        )
    finally:
        store.close()
