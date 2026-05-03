"""Tests for _search_canon_channel() — FTS-based canon recall via MemoryStore."""

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.bootstrap.core import _sync_canon_store
from co_cli.deps import CoDeps, CoSessionState
from co_cli.memory.memory_store import MemoryStore
from co_cli.tools.memory.recall import _search_canon_channel
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


def test_canon_hit_body_contains_query_content(tmp_path: Path) -> None:
    """A matching query returns a hit whose body carries the query token — not a snippet."""
    ctx = _make_ctx_with_store(tmp_path, personality="tars")
    try:
        hits = _search_canon_channel(ctx, "humor deadpan")
        assert len(hits) >= 1, "expected at least 1 canon hit for 'humor deadpan'"
        top = hits[0]
        assert top["channel"] == "canon"
        assert top["role"] == "tars"
        assert "humor" in top["body"].lower(), (
            "body must contain query-relevant content — body is not a snippet"
        )
    finally:
        ctx.deps.memory_store.close()


def test_canon_body_is_full_text_not_snippet(tmp_path: Path) -> None:
    """body field contains the complete post-frontmatter file text, not a FTS5 snippet()."""
    ctx = _make_ctx_with_store(tmp_path, personality="tars")
    try:
        hits = _search_canon_channel(ctx, "humor deadpan")
        assert hits, "expected at least 1 hit"
        top = hits[0]
        # The full humor file body is ~700 chars; FTS5 snippet() caps at ~200 chars.
        assert len(top["body"]) > 200, (
            f"body length {len(top['body'])} is too short — expected full file text, not a snippet"
        )
    finally:
        ctx.deps.memory_store.close()


def test_canon_returns_empty_when_store_is_none(tmp_path: Path) -> None:
    """_search_canon_channel returns [] when memory_store is None — grep backend degradation."""
    ctx = _make_ctx_no_store(tmp_path, personality="tars")
    hits = _search_canon_channel(ctx, "humor")
    assert hits == [], "expected [] when memory_store is None"


def test_canon_returns_empty_when_personality_none(tmp_path: Path) -> None:
    """_search_canon_channel returns [] when personality is None — canon is role-gated."""
    ctx = _make_ctx_with_store(tmp_path, personality=None)
    try:
        hits = _search_canon_channel(ctx, "humor")
        assert hits == [], "expected [] when personality=None"
    finally:
        ctx.deps.memory_store.close()
