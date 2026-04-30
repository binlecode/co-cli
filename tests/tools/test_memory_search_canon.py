"""Functional tests for memory_search canon channel wiring."""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.agent.core import build_agent
from co_cli.deps import CoDeps
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.shell_backend import ShellBackend

_AGENT = build_agent(config=SETTINGS)
_MODEL = _AGENT.model


def _make_ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=_MODEL, usage=RunUsage(), tool_name="memory_search")


def _make_deps(tmp_path: Path, *, personality: str = "tars") -> CoDeps:
    config = SETTINGS.model_copy(update={"personality": personality, "mcp_servers": []})
    return CoDeps(
        shell=ShellBackend(),
        config=config,
        sessions_dir=tmp_path / "sessions",
        tool_results_dir=tmp_path / "tool-results",
        knowledge_dir=tmp_path / "knowledge",
    )


@pytest.mark.asyncio
async def test_memory_search_canon_returns_hits(tmp_path: Path) -> None:
    """Canon-invoking query returns hits with channel='canon' merged into all_results."""
    deps = _make_deps(tmp_path, personality="tars")
    ctx = _make_ctx(deps)
    result = await memory_search(ctx, query="humor tactical")
    assert result.metadata is not None
    canon = [r for r in result.metadata["results"] if r["channel"] == "canon"]
    assert len(canon) >= 1, "Expected canon hits for 'humor tactical' with tars personality"
    assert all(r["role"] == "tars" for r in canon)


@pytest.mark.asyncio
async def test_memory_search_canon_header_in_output(tmp_path: Path) -> None:
    """Result text includes '**Character canon:**' when canon hits are present."""
    deps = _make_deps(tmp_path, personality="tars")
    ctx = _make_ctx(deps)
    result = await memory_search(ctx, query="humor tactical")
    assert "Character canon:" in result.return_value


@pytest.mark.asyncio
async def test_memory_search_canon_capped_at_recall_limit(tmp_path: Path) -> None:
    """Canon results respect character_recall_limit regardless of how many files match."""
    config = SETTINGS.model_copy(
        update={
            "personality": "tars",
            "mcp_servers": [],
            "knowledge": SETTINGS.knowledge.model_copy(update={"character_recall_limit": 1}),
        }
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        sessions_dir=tmp_path / "sessions",
        tool_results_dir=tmp_path / "tool-results",
        knowledge_dir=tmp_path / "knowledge",
    )
    ctx = _make_ctx(deps)
    result = await memory_search(ctx, query="tars humor mission directive loyalty warmth")
    canon = [r for r in result.metadata["results"] if r["channel"] == "canon"]
    assert len(canon) <= 1


@pytest.mark.asyncio
async def test_memory_search_empty_query_bypasses_canon(tmp_path: Path) -> None:
    """Empty query uses browse mode — _search_canon_channel is never reached."""
    deps = _make_deps(tmp_path, personality="tars")
    ctx = _make_ctx(deps)
    result = await memory_search(ctx, query="")
    assert result.metadata is not None
    # Browse mode returns session-channel results only (or empty) — no canon channel
    assert not any(r["channel"] == "canon" for r in result.metadata["results"])


@pytest.mark.asyncio
async def test_memory_search_no_personality_skips_canon(tmp_path: Path) -> None:
    """Empty personality produces no canon results and no exception."""
    config = SETTINGS.model_copy(update={"mcp_servers": []})
    # Force personality to empty string, bypassing validator via model_copy
    object.__setattr__(config, "personality", "")
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        sessions_dir=tmp_path / "sessions",
        tool_results_dir=tmp_path / "tool-results",
        knowledge_dir=tmp_path / "knowledge",
    )
    ctx = _make_ctx(deps)
    result = await memory_search(ctx, query="humor tactical")
    assert not any(r["channel"] == "canon" for r in result.metadata["results"])


@pytest.mark.asyncio
async def test_memory_search_stopword_only_query_no_canon(tmp_path: Path) -> None:
    """Stopword-only query produces no canon results."""
    deps = _make_deps(tmp_path, personality="tars")
    ctx = _make_ctx(deps)
    result = await memory_search(ctx, query="the and a is of")
    assert not any(r["channel"] == "canon" for r in result.metadata["results"])
