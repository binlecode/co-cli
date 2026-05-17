"""Tests for knowledge_search — ranked FTS over knowledge artifacts."""

import asyncio
import json
import logging
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS
from tests._timeouts import FILE_DB_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.memory_store import MemoryStore
from co_cli.memory.service import reindex, save_artifact
from co_cli.observability import tracing
from co_cli.tools.memory.manage import _handle_create
from co_cli.tools.memory.recall import _ARTIFACTS_USER_CAP, knowledge_search
from co_cli.tools.shell_backend import ShellBackend

_FTS5_CONFIG = SETTINGS.knowledge.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_TEST_SETTINGS = SETTINGS.model_copy(update={"knowledge": _FTS5_CONFIG})


def _make_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(config=_TEST_SETTINGS, memory_db_path=tmp_path / "search.db")


def _make_deps(tmp_path: Path, store: MemoryStore) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=_TEST_SETTINGS,
        session=CoSessionState(),
        knowledge_dir=tmp_path / "knowledge",
        memory_store=store,
    )


def _ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage())


@pytest.fixture
def isolated_spans_log(tmp_path: Path):
    """Isolated spans log with clean state; restores logger state on teardown."""
    logger = logging.getLogger("co_cli.observability.spans")
    saved_handlers = list(logger.handlers)
    saved_patterns = list(tracing._COMPILED_PATTERNS)
    for h in saved_handlers:
        logger.removeHandler(h)
    tracing._SPAN_STACK.set(())

    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)
    yield log

    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    for h in saved_handlers:
        logger.addHandler(h)
    tracing._COMPILED_PATTERNS = saved_patterns


def _seed(
    knowledge_dir: Path,
    store: MemoryStore,
    *,
    content: str,
    kind: str,
    title: str,
) -> None:
    r = save_artifact(knowledge_dir, content=content, artifact_kind=kind, title=title)
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


@pytest.mark.asyncio
async def test_knowledge_search_returns_hit_with_correct_field_shape(tmp_path: Path) -> None:
    """knowledge_search must return hits with the expected field set.

    Failure mode: renaming/removing a result field silently breaks downstream
    callers (knowledge_view, display logic) that rely on filename_stem and path.
    """
    store = _make_store(tmp_path)
    try:
        _seed(
            tmp_path / "knowledge",
            store,
            content="zqpuniqtoken7x content about something",
            kind="note",
            title="test shape",
        )
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await knowledge_search(ctx, query="zqpuniqtoken7x")

        results = result.metadata.get("results", [])
        assert results, "expected at least one hit"
        r = results[0]
        for field in ("kind", "title", "snippet", "score", "path", "filename_stem"):
            assert field in r, f"missing field {field!r} in result: {r}"
        assert "channel" not in r, f"result must not carry 'channel' field: {r}"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_knowledge_search_empty_query_browse_returns_user_kind(tmp_path: Path) -> None:
    """Empty-query knowledge_search with kinds=['user'] returns user-kind artifacts.

    Failure mode: browse mode not filtering by kinds → non-user artifacts appear.
    """
    store = _make_store(tmp_path)
    try:
        _seed(
            tmp_path / "knowledge",
            store,
            content="user preference about editors",
            kind="user",
            title="editor prefs",
        )
        _seed(
            tmp_path / "knowledge",
            store,
            content="article about vim configuration",
            kind="article",
            title="vim article",
        )
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await knowledge_search(ctx, query="", kinds=["user"])

        results = result.metadata.get("results", [])
        assert results, "expected at least one result"
        assert all(r["kind"] == "user" for r in results), (
            f"browse with kinds=['user'] must only return user-kind: {results}"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_knowledge_search_no_match_returns_empty(tmp_path: Path) -> None:
    """knowledge_search with a no-match query returns count=0 and empty results, no error.

    Failure mode: returning an error instead of an empty result list causes the agent
    to treat a cache miss as a tool failure rather than a genuine empty recall.
    """
    store = _make_store(tmp_path)
    try:
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await knowledge_search(ctx, query="xyzzy_absolutely_no_match_7q9r")

        assert result.metadata is not None
        assert result.metadata.get("error") is not True, (
            f"no-match must not return tool_error: {result.return_value!r}"
        )
        assert result.metadata.get("count", 0) == 0
        assert result.metadata.get("results", []) == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_knowledge_search_kinds_filter_respected(tmp_path: Path) -> None:
    """knowledge_search with kinds=['rule'] must only return rule-kind hits.

    Failure mode: kinds filter not applied → user preference hits pollute rule results.
    """
    store = _make_store(tmp_path)
    try:
        _seed(
            tmp_path / "knowledge",
            store,
            content="filterkind marker rule for coding conventions",
            kind="rule",
            title="coding convention",
        )
        _seed(
            tmp_path / "knowledge",
            store,
            content="filterkind marker user preference about something",
            kind="user",
            title="user pref",
        )
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await knowledge_search(ctx, query="filterkind marker", kinds=["rule"])

        results = result.metadata.get("results", [])
        assert results, "expected at least one rule hit"
        assert all(r["kind"] == "rule" for r in results), (
            f"kinds=['rule'] filter must exclude non-rule results: {results}"
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_knowledge_search_user_priority_pass_cap_honoured(tmp_path: Path) -> None:
    """knowledge_search must cap user-kind hits at _ARTIFACTS_USER_CAP per call.

    Failure mode: unbounded user results crowd out other kinds and waste context tokens.
    """
    store = _make_store(tmp_path)
    try:
        cap_token = "usercap_marker_xq8z"
        for i in range(_ARTIFACTS_USER_CAP + 2):
            _seed(
                tmp_path / "knowledge",
                store,
                content=f"{cap_token} user preference number {i}",
                kind="user",
                title=f"user pref {i}",
            )
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            result = await knowledge_search(ctx, query=cap_token, kinds=["user"])

        user_hits = [r for r in result.metadata.get("results", []) if r["kind"] == "user"]
        assert len(user_hits) <= _ARTIFACTS_USER_CAP, (
            f"user hits must be capped at {_ARTIFACTS_USER_CAP}, got {len(user_hits)}"
        )
    finally:
        store.close()


def test_knowledge_search_disk_scan_fallback_when_no_store(tmp_path: Path) -> None:
    """knowledge_search falls back to disk scan when memory_store is None.

    Failure mode: no-store path returns empty instead of available artifacts,
    breaking cold-start recall in environments without FTS5 indexing.
    """

    from pydantic_ai.usage import RunUsage

    from co_cli.tools.memory.recall import _list_artifacts

    knowledge_dir = tmp_path / "knowledge"
    store_tmp = _make_store(tmp_path)
    try:
        _seed(
            knowledge_dir,
            store_tmp,
            content="disk fallback content here",
            kind="note",
            title="disk artifact",
        )
    finally:
        store_tmp.close()

    deps = CoDeps(
        shell=ShellBackend(),
        config=_TEST_SETTINGS,
        session=CoSessionState(),
        knowledge_dir=knowledge_dir,
        memory_store=None,
    )
    from co_cli.observability.tracing import current_span

    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    results = _list_artifacts(ctx, kinds=None, limit=10, span=current_span())

    assert len(results) >= 1, f"expected disk fallback results, got {results}"
    for r in results:
        assert "channel" not in r, f"disk fallback result must not have channel: {r}"
        assert "kind" in r
        assert "path" in r
        assert "filename_stem" in r


@pytest.mark.asyncio
async def test_knowledge_manage_create_emits_span(
    isolated_spans_log: Path, tmp_path: Path
) -> None:
    """knowledge_manage(action='create') emits a co.knowledge.knowledge_manage.create record."""
    store = _make_store(tmp_path)
    try:
        deps = _make_deps(tmp_path, store)
        ctx = _ctx(deps)

        async with asyncio.timeout(FILE_DB_TIMEOUT_SECS):
            await _handle_create(
                ctx, name="span test artifact", content="test content", kind="note"
            )
    finally:
        store.close()

    logger = logging.getLogger("co_cli.observability.spans")
    for h in logger.handlers:
        h.flush()
    records = [
        json.loads(line) for line in isolated_spans_log.read_text().splitlines() if line.strip()
    ]
    create_records = [r for r in records if r["name"] == "co.knowledge.knowledge_manage.create"]
    assert create_records, "expected a co.knowledge.knowledge_manage.create span record"
    attrs = create_records[0]["attributes"]
    assert attrs.get("knowledge.artifact_kind") == "note"
    assert create_records[0]["status"] == "OK"
