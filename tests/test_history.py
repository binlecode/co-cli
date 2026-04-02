"""Functional tests for context history processors and compaction."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli._model_factory import ModelRegistry, ResolvedModel
from co_cli.agent import build_agent
from co_cli.commands._commands import CommandContext, LocalOnly, ReplaceTranscript, dispatch
from co_cli.config import settings, ROLE_SUMMARIZATION
from co_cli.context._history import truncate_history_window
from co_cli.context._types import Compaction
from co_cli.deps import CoDeps, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
# Cache agent model reference for RunContext construction — no LLM call made here.
_AGENT = build_agent(config=_CONFIG)


def _make_processor_ctx(max_history_messages: int = 6) -> RunContext:
    """Real RunContext for history processor tests (no LLM call)."""
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(max_history_messages=max_history_messages),
    )
    return RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())


def _make_compact_ctx(message_history: list | None = None) -> CommandContext:
    """Real CommandContext with model registry for /compact dispatch tests."""
    deps = CoDeps(
        shell=ShellBackend(), model_registry=_REGISTRY,
        config=_CONFIG,
        session=CoSessionState(session_id="test-history"),
    )
    return CommandContext(
        message_history=message_history or [],
        deps=deps,
        agent=_AGENT,
    )


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _make_messages(n: int) -> list:
    """Alternating user/assistant messages; index 1 is always an assistant with TextPart.

    With max_history_messages=6 and n=10:
      _find_first_run_end → 1  (first ModelResponse with TextPart)
      head_end = 2
      tail_count = max(4, 6//2) = 4
      tail_start = max(2, 10-4) = 6
      dropped = messages[2:6] (4 messages)
    """
    msgs = []
    for i in range(n // 2):
        msgs.append(_user(f"user turn {i}"))
        msgs.append(_assistant(f"assistant turn {i}"))
    if n % 2:
        msgs.append(_user(f"user turn {n // 2}"))
    return msgs


# ---------------------------------------------------------------------------
# truncate_history_window — static marker and precomputed paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncate_history_window_static_marker_when_no_precomputed():
    """No precomputed result → static marker injected (no LLM call)."""
    msgs = _make_messages(10)
    ctx = _make_processor_ctx(max_history_messages=6)
    # precomputed_compaction is None by default
    result = await truncate_history_window(ctx, msgs)
    marker_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    ]
    assert any("[Earlier conversation trimmed" in t for t in marker_texts)
    assert len(result) < len(msgs)


@pytest.mark.asyncio
async def test_truncate_history_window_uses_precomputed_result():
    """Valid precomputed result is used — summary text appears in output."""
    msgs = _make_messages(10)
    ctx = _make_processor_ctx(max_history_messages=6)
    ctx.deps.runtime.precomputed_compaction = Compaction(
        summary_text="PRECOMPUTED_SUMMARY_SENTINEL",
        head_end=2,
        tail_start=6,
        message_count=10,
    )
    result = await truncate_history_window(ctx, msgs)
    all_content = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    ]
    assert any("PRECOMPUTED_SUMMARY_SENTINEL" in t for t in all_content)


@pytest.mark.asyncio
async def test_truncate_history_window_stale_precomputed_uses_static_marker():
    """Precomputed result with wrong head_end/tail_start → static marker, not summary."""
    msgs = _make_messages(10)
    ctx = _make_processor_ctx(max_history_messages=6)
    ctx.deps.runtime.precomputed_compaction = Compaction(
        summary_text="STALE_MARKER_SENTINEL",
        head_end=999,
        tail_start=999,
        message_count=10,
    )
    result = await truncate_history_window(ctx, msgs)
    all_content = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    ]
    assert not any("STALE_MARKER_SENTINEL" in t for t in all_content)
    assert any("[Earlier conversation trimmed" in t for t in all_content)


# ---------------------------------------------------------------------------
# /compact dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_produces_two_message_history():
    """/compact on non-empty history returns ReplaceTranscript with 2 messages and compaction_applied."""
    msgs = _make_messages(6)
    ctx = _make_compact_ctx(message_history=msgs)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await dispatch("/compact", ctx)
    assert isinstance(result, ReplaceTranscript)
    assert len(result.history) == 2
    assert result.compaction_applied is True
