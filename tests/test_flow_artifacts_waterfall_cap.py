"""Tests for _search_artifacts() waterfall-pass dual cap (count and size)."""

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.memory_store import MemoryStore
from co_cli.tools.memory.recall import (
    _ARTIFACTS_WATERFALL_CHUNK_CAP,
    _ARTIFACTS_WATERFALL_SIZE_CAP,
    _search_artifacts,
)
from co_cli.tools.shell_backend import ShellBackend

_KEYWORD = "waterfall_test_distinctive_keyword_abc"

_FTS5_CONFIG = SETTINGS.knowledge.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
        "chunk_size": 600,
        "chunk_overlap": 0,
    }
)
_STORE_CONFIG = SETTINGS.model_copy(update={"knowledge": _FTS5_CONFIG})


def _make_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(config=_STORE_CONFIG, memory_db_path=tmp_path / "search.db")


def _make_ctx(tmp_path: Path, store: MemoryStore) -> RunContext[CoDeps]:
    deps = CoDeps(
        shell=ShellBackend(),
        config=_STORE_CONFIG,
        session=CoSessionState(),
        memory_store=store,
        sessions_dir=tmp_path / "sessions",
        knowledge_dir=tmp_path / "knowledge",
    )
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _write_artifact(directory: Path, name: str, artifact_kind: str, body: str) -> Path:
    """Write a minimal knowledge artifact .md file with valid YAML frontmatter."""
    content = (
        "---\n"
        f"id: {name}\n"
        f"created: 2025-01-01T00:00:00\n"
        f"kind: knowledge\n"
        f"artifact_kind: {artifact_kind}\n"
        f"title: {name}\n"
        "---\n"
        f"{body}\n"
    )
    path = directory / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_waterfall_count_cap(tmp_path: Path) -> None:
    """Waterfall pass stops at _ARTIFACTS_WATERFALL_CHUNK_CAP even when more artifacts match.

    Regression: if deleted, the count cap is no longer tested and unlimited results
    could exhaust context window in production.
    """
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    # 6 rule artifacts, each ~100 chars — well under size cap in aggregate (6 x 100 = 600 < 2000)
    short_body = f"{_KEYWORD} " + "x" * 90
    for i in range(6):
        _write_artifact(knowledge_dir, f"rule_{i:02d}", "rule", short_body)

    store = _make_store(tmp_path)
    try:
        store.sync_dir("knowledge", knowledge_dir)
        ctx = _make_ctx(tmp_path, store)
        results = _search_artifacts(ctx, _KEYWORD, kinds=["rule"], limit=100)
        assert len(results) <= _ARTIFACTS_WATERFALL_CHUNK_CAP, (
            f"expected at most {_ARTIFACTS_WATERFALL_CHUNK_CAP} results, got {len(results)}"
        )
    finally:
        store.close()


def test_waterfall_size_cap(tmp_path: Path) -> None:
    """Waterfall pass stops when cumulative full-chunk chars reach _ARTIFACTS_WATERFALL_SIZE_CAP.

    With chunk_size=600 each ~500-char note body fits in one chunk. Size accumulates as
    4 x 500 = 2000, which equals the size cap before the count cap of 5 is reached.

    Regression: if deleted, the size cap is no longer tested and oversized context
    payloads could be injected into the agent turn unchecked.
    """
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()

    # 6 note artifacts, each ~500 chars — 4 x 500 = 2000 hits the size cap before count cap of 5
    long_body = f"{_KEYWORD} " + "y" * 480
    # Pad to exactly 500 chars total (keyword is 38 chars + space = 39, pad to 500 - 39 = 461)
    filler_len = 500 - len(_KEYWORD) - 1
    long_body = f"{_KEYWORD} " + "y" * filler_len
    for i in range(6):
        _write_artifact(knowledge_dir, f"note_{i:02d}", "note", long_body)

    store = _make_store(tmp_path)
    try:
        store.sync_dir("knowledge", knowledge_dir)
        ctx = _make_ctx(tmp_path, store)
        results = _search_artifacts(ctx, _KEYWORD, kinds=["note"], limit=100)
        # Each chunk is ~500 chars; size cap is 2000; count cap is 5.
        # The check `if total_chars >= _ARTIFACTS_WATERFALL_SIZE_CAP` fires BEFORE appending
        # the 5th item (4 x 500 = 2000 >= 2000), so exactly 4 results are returned.
        assert len(results) == 4, (
            f"expected 4 results (size cap reached at 4 x ~500 chars), got {len(results)}"
        )
        assert len(results) < _ARTIFACTS_WATERFALL_CHUNK_CAP, (
            "size cap should have triggered before count cap"
        )
        assert _ARTIFACTS_WATERFALL_SIZE_CAP // len(results) <= 500, (
            "sanity: each result body should be ~500 chars"
        )
    finally:
        store.close()
