"""Tests for canon recall via _search_artifacts() — FTS-based, channel='artifacts', kind='canon'."""

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.bootstrap.core import _sync_canon_store
from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.memory_store import MemoryStore
from co_cli.tools.memory.recall import _ARTIFACTS_CANON_CAP, _search_artifacts
from co_cli.tools.shell_backend import ShellBackend

_FTS5_CONFIG = SETTINGS.knowledge.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_STORE_CONFIG = SETTINGS.model_copy(update={"knowledge": _FTS5_CONFIG})


class _SilentFrontend:
    def on_status(self, msg: str) -> None:
        pass


def _make_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(config=_STORE_CONFIG, memory_db_path=tmp_path / "search.db")


def _make_ctx_with_store(tmp_path: Path, *, personality: str | None) -> RunContext[CoDeps]:
    """RunContext with a real MemoryStore; canon indexed from real tars soul files when personality set."""
    store = _make_store(tmp_path)
    config = SETTINGS.model_copy(update={"personality": personality})
    if personality:
        _sync_canon_store(store, config, _SilentFrontend())
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        session=CoSessionState(),
        memory_store=store,
        sessions_dir=tmp_path / "sessions",
        knowledge_dir=tmp_path / "knowledge",
    )
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _make_ctx_no_store(tmp_path: Path, *, personality: str | None) -> RunContext[CoDeps]:
    """RunContext with memory_store=None — simulates the grep backend degradation path."""
    config = SETTINGS.model_copy(update={"personality": personality})
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        session=CoSessionState(),
        memory_store=None,
        sessions_dir=tmp_path / "sessions",
        knowledge_dir=tmp_path / "knowledge",
    )
    return RunContext(deps=deps, model=None, usage=RunUsage())


def test_canon_hit_carries_artifacts_channel_and_canon_kind(tmp_path: Path) -> None:
    """Canon results flow through the artifacts channel with kind='canon'."""
    ctx = _make_ctx_with_store(tmp_path, personality="tars")
    try:
        hits = _search_artifacts(ctx, "humor deadpan", kinds=None, limit=10)
        canon_hits = [h for h in hits if h.get("kind") == "canon"]
        assert len(canon_hits) >= 1, "expected at least 1 canon hit for 'humor deadpan'"
        top = canon_hits[0]
        assert top["channel"] == "artifacts"
        assert top["kind"] == "canon"
    finally:
        ctx.deps.memory_store.close()


def test_canon_snippet_is_full_body_not_fts_snippet(tmp_path: Path) -> None:
    """snippet field for canon carries the complete post-frontmatter file text, not a FTS5 snippet()."""
    ctx = _make_ctx_with_store(tmp_path, personality="tars")
    try:
        hits = _search_artifacts(ctx, "humor deadpan", kinds=None, limit=10)
        canon_hits = [h for h in hits if h.get("kind") == "canon"]
        assert canon_hits, "expected at least 1 canon hit"
        top = canon_hits[0]
        # real tars humor file body is ~700 chars; FTS5 snippet() would cap at ~200
        assert len(top["snippet"]) > 200, (
            f"snippet length {len(top['snippet'])} is too short — expected full file text, not FTS5 snippet"
        )
    finally:
        ctx.deps.memory_store.close()


def test_canon_cap_honored(tmp_path: Path) -> None:
    """Canon hits respect _ARTIFACTS_CANON_CAP."""
    ctx = _make_ctx_with_store(tmp_path, personality="tars")
    try:
        hits = _search_artifacts(ctx, "humor deadpan", kinds=None, limit=100)
        canon_hits = [h for h in hits if h.get("kind") == "canon"]
        assert len(canon_hits) <= _ARTIFACTS_CANON_CAP, (
            f"expected at most {_ARTIFACTS_CANON_CAP} canon hits, got {len(canon_hits)}"
        )
    finally:
        ctx.deps.memory_store.close()


def test_kinds_canon_filter_returns_only_canon(tmp_path: Path) -> None:
    """kinds=['canon'] isolates canon-only results."""
    ctx = _make_ctx_with_store(tmp_path, personality="tars")
    try:
        hits = _search_artifacts(ctx, "humor deadpan", kinds=["canon"], limit=10)
        assert len(hits) >= 1, "expected at least 1 hit with kinds=['canon']"
        for h in hits:
            assert h["kind"] == "canon", f"expected kind='canon', got {h['kind']!r}"
            assert h["channel"] == "artifacts"
    finally:
        ctx.deps.memory_store.close()


def test_canon_returns_empty_when_store_is_none(tmp_path: Path) -> None:
    """_search_artifacts returns [] for canon when memory_store is None (grep degradation)."""
    ctx = _make_ctx_no_store(tmp_path, personality="tars")
    hits = _search_artifacts(ctx, "humor", kinds=["canon"], limit=10)
    assert hits == [], "expected [] when memory_store is None and kinds=['canon']"


def test_canon_returns_empty_when_personality_none(tmp_path: Path) -> None:
    """Canon pass yields no results when personality is None — canon is role-gated."""
    ctx = _make_ctx_with_store(tmp_path, personality=None)
    try:
        hits = _search_artifacts(ctx, "humor", kinds=None, limit=10)
        canon_hits = [h for h in hits if h.get("kind") == "canon"]
        assert canon_hits == [], "expected no canon hits when personality=None"
    finally:
        ctx.deps.memory_store.close()
