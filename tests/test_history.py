"""Functional tests for context history processors and compaction."""

import asyncio
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

from co_cli._model_factory import ModelRegistry
from co_cli.agent import build_agent
from co_cli.commands._commands import CommandContext, ReplaceTranscript, dispatch
from co_cli.config import settings
from co_cli.context._history import truncate_history_window
from co_cli.deps import CoDeps, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

_CONFIG = CoConfig.from_settings(settings, cwd=Path.cwd())
_REGISTRY = ModelRegistry.from_config(_CONFIG)
# Cache agent model reference for RunContext construction — no LLM call made here.
_AGENT = build_agent(config=_CONFIG)


def _make_processor_ctx() -> RunContext:
    """Real RunContext for history processor tests (no LLM call).

    Uses a tiny Ollama budget (llm_num_ctx=30) so the char-estimate
    from _make_messages(10) (~33 tokens) exceeds int(30 * 0.85) = 25.
    """
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(llm_provider="ollama-openai", llm_num_ctx=30),
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

    With n=10 and tail_count = max(4, 10//2) = 5:
      find_first_run_end → 1  (first ModelResponse with TextPart)
      head_end = 2
      tail_start = max(2, 10-5) = 5
      dropped = messages[2:5] (3 messages)
    """
    msgs = []
    for i in range(n // 2):
        msgs.append(_user(f"user turn {i}"))
        msgs.append(_assistant(f"assistant turn {i}"))
    if n % 2:
        msgs.append(_user(f"user turn {n // 2}"))
    return msgs


# ---------------------------------------------------------------------------
# truncate_history_window — inline summarisation and guard paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncate_history_window_static_marker_when_no_model_registry():
    """model_registry=None → static marker injected (guard path, no LLM call)."""
    msgs = _make_messages(10)
    ctx = _make_processor_ctx()
    # model_registry is None by default — guard skips LLM, uses static marker
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


# ---------------------------------------------------------------------------
# Circuit breaker — skip LLM after 3 consecutive failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_skips_llm_after_three_failures():
    """compaction_failure_count >= 3 → static marker without LLM call."""
    msgs = _make_messages(10)
    deps = CoDeps(
        shell=ShellBackend(),
        config=CoConfig(llm_provider="ollama-openai", llm_num_ctx=30),
        model_registry=_REGISTRY,
    )
    deps.runtime.compaction_failure_count = 3
    ctx = RunContext(deps=deps, model=_AGENT.model, usage=RunUsage())
    result = await truncate_history_window(ctx, msgs)
    marker_texts = [
        p.content
        for m in result
        if isinstance(m, ModelRequest)
        for p in m.parts
        if hasattr(p, "content") and isinstance(p.content, str)
    ]
    # Circuit breaker active → static marker, no LLM call
    assert any("[Earlier conversation trimmed" in t for t in marker_texts)
    assert len(result) < len(msgs)
    # Failure count unchanged (no LLM attempt was made)
    assert deps.runtime.compaction_failure_count == 3


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
