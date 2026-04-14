"""Functional tests for the session_search tool and its registration."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.context.session import session_filename
from co_cli.context.transcript import append_messages
from co_cli.deps import CoDeps, VisibilityPolicyEnum
from co_cli.session_index._store import SessionIndex
from co_cli.tools.session_search import session_search
from co_cli.tools.shell_backend import ShellBackend

_CONFIG = make_settings()
_AGENT = build_agent(config=_CONFIG)
_MODEL = _AGENT.model


def _make_ctx(deps: CoDeps) -> RunContext:
    return RunContext(
        deps=deps,
        model=_MODEL,
        usage=RunUsage(),
        tool_name="session_search",
    )


def _make_deps(tmp_path: Path, *, session_index: SessionIndex | None = None) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=_CONFIG,
        session_index=session_index,
        sessions_dir=tmp_path / "sessions",
        tool_results_dir=tmp_path / "tool-results",
    )


def _write_indexed_session(sessions_dir: Path, store: SessionIndex, *, content: str) -> str:
    """Write a session JSONL and index it; return the uuid8 session_id."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 4, 14, 10, 0, 0, tzinfo=UTC)
    name = session_filename(now, "testsess-0000-0000-0000-000000000000")
    path = sessions_dir / name
    append_messages(
        path,
        [
            ModelRequest(parts=[UserPromptPart(content=content)]),
            ModelResponse(parts=[TextPart(content=f"Response to: {content}")]),
        ],
    )
    store.index_session(path)
    return "testsess"


# ---------------------------------------------------------------------------
# Test 1: tool returns results when session_index has indexed data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_search_returns_results_when_indexed(tmp_path: Path) -> None:
    """session_search returns ToolReturn with session results when index has data."""
    db_path = tmp_path / "session-index.db"
    store = SessionIndex(db_path)
    try:
        sessions_dir = tmp_path / "sessions"
        _write_indexed_session(
            sessions_dir,
            store,
            content="Explain how the Python asyncio event loop works",
        )

        deps = _make_deps(tmp_path, session_index=store)
        ctx = _make_ctx(deps)

        result = await session_search(ctx, "asyncio event loop", limit=3)

        assert result.return_value, "ToolReturn must have non-empty return_value"
        assert (
            "asyncio" in result.return_value.lower() or "event" in result.return_value.lower()
        ), "Result text must reference the query terms"
        assert result.metadata is not None
        assert result.metadata["count"] >= 1, (
            f"count metadata must be >= 1, got {result.metadata['count']}"
        )
        results_list = result.metadata["results"]
        assert len(results_list) >= 1
        assert results_list[0]["session_id"] == "testsess"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Test 2: tool returns graceful message when session_index is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_search_graceful_when_index_none(tmp_path: Path) -> None:
    """session_search returns a graceful message when deps.session_index is None."""
    deps = _make_deps(tmp_path, session_index=None)
    ctx = _make_ctx(deps)

    result = await session_search(ctx, "anything")

    assert result.return_value, "ToolReturn must have non-empty return_value"
    assert result.metadata is not None
    assert result.metadata["count"] == 0, (
        f"count must be 0 when index is None, got {result.metadata['count']}"
    )


# ---------------------------------------------------------------------------
# Test 3: tool is registered with DEFERRED visibility
# ---------------------------------------------------------------------------


def test_session_search_registered_with_deferred_visibility() -> None:
    """session_search must appear in tool_index with DEFERRED visibility."""
    from co_cli.agent._core import build_tool_registry

    registry = build_tool_registry(_CONFIG)
    tool_index = registry.tool_index

    assert "session_search" in tool_index, "'session_search' must be registered in tool_index"
    assert tool_index["session_search"].visibility == VisibilityPolicyEnum.DEFERRED, (
        f"session_search visibility must be DEFERRED, got {tool_index['session_search'].visibility}"
    )
