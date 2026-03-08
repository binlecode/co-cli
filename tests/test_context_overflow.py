"""Functional tests for context overflow detection in run_turn().

Tests verify that on_status fires the correct warning/overflow message based on
the input_tokens / ollama_num_ctx ratio, and that Gemini turns are skipped.
"""

import pytest
from pydantic_ai import AgentRunResult, AgentRunResultEvent
from pydantic_ai._agent_graph import GraphAgentState
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from co_cli._orchestrate import run_turn
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli._shell_backend import ShellBackend
from tests.test_orchestrate import RecordingFrontend, StaticEventAgent


def _make_result_with_usage(
    text: str, input_tokens: int, finish_reason: str = "stop"
) -> AgentRunResult:
    """Build a minimal AgentRunResult with controlled input_tokens for overflow tests."""
    state = GraphAgentState(
        message_history=[
            ModelRequest(parts=[UserPromptPart(content="hi")]),
            ModelResponse(parts=[TextPart(content=text)], finish_reason=finish_reason),
        ],
        usage=RunUsage(input_tokens=input_tokens),
    )
    return AgentRunResult(output=text, _state=state)


def _make_deps(
    *,
    llm_provider: str = "ollama",
    ollama_num_ctx: int = 65536,
    ctx_warn_threshold: float = 0.85,
    ctx_overflow_threshold: float = 1.0,
) -> CoDeps:
    return CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            llm_provider=llm_provider,
            ollama_num_ctx=ollama_num_ctx,
            ctx_warn_threshold=ctx_warn_threshold,
            ctx_overflow_threshold=ctx_overflow_threshold,
        ),
    )


# ---------------------------------------------------------------------------
# Test 1 — warn fires at 90% (above 0.85 threshold)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warn_fires_at_90_percent():
    """on_status must emit 'full' warning when ratio >= ctx_warn_threshold and < ctx_overflow_threshold."""
    # 58982 / 65536 ≈ 90%
    result = _make_result_with_usage("ok", input_tokens=58982)
    frontend = RecordingFrontend()
    agent = StaticEventAgent([AgentRunResultEvent(result=result)])
    deps = _make_deps(ollama_num_ctx=65536, ctx_warn_threshold=0.85, ctx_overflow_threshold=1.0)

    turn = await run_turn(
        agent=agent,
        user_input="hello",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    status_messages = [msg for kind, msg in frontend.events if kind == "status"]
    assert any("full" in msg for msg in status_messages)
    assert not any("limit reached" in msg for msg in status_messages)


# ---------------------------------------------------------------------------
# Test 2 — overflow fires at exactly 100% (== ctx_overflow_threshold)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overflow_fires_at_100_percent():
    """on_status must emit 'limit reached' error when ratio >= ctx_overflow_threshold."""
    # 65536 / 65536 = 100%
    result = _make_result_with_usage("ok", input_tokens=65536)
    frontend = RecordingFrontend()
    agent = StaticEventAgent([AgentRunResultEvent(result=result)])
    deps = _make_deps(ollama_num_ctx=65536, ctx_warn_threshold=0.85, ctx_overflow_threshold=1.0)

    turn = await run_turn(
        agent=agent,
        user_input="hello",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    status_messages = [msg for kind, msg in frontend.events if kind == "status"]
    assert any("limit reached" in msg for msg in status_messages)


# ---------------------------------------------------------------------------
# Test 3 — silent below 50% (no ctx status messages)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_silent_below_threshold():
    """No ctx status messages should be emitted when ratio is well below ctx_warn_threshold."""
    # 32768 / 65536 = 50%
    result = _make_result_with_usage("ok", input_tokens=32768)
    frontend = RecordingFrontend()
    agent = StaticEventAgent([AgentRunResultEvent(result=result)])
    deps = _make_deps(ollama_num_ctx=65536, ctx_warn_threshold=0.85, ctx_overflow_threshold=1.0)

    turn = await run_turn(
        agent=agent,
        user_input="hello",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    status_messages = [msg for kind, msg in frontend.events if kind == "status"]
    assert not any("full" in msg for msg in status_messages)
    assert not any("limit reached" in msg for msg in status_messages)


# ---------------------------------------------------------------------------
# Test 4 — Gemini provider skipped (even at 200%)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_provider_skipped():
    """No ctx status messages must fire for Gemini, even at 200% of num_ctx."""
    # 131072 / 65536 = 200% — would overflow if provider were ollama
    result = _make_result_with_usage("ok", input_tokens=131072)
    frontend = RecordingFrontend()
    agent = StaticEventAgent([AgentRunResultEvent(result=result)])
    # Thresholds set extremely low (0.01) to guarantee they'd trigger on ollama
    deps = _make_deps(
        llm_provider="gemini",
        ollama_num_ctx=65536,
        ctx_warn_threshold=0.01,
        ctx_overflow_threshold=0.01,
    )

    turn = await run_turn(
        agent=agent,
        user_input="hello",
        deps=deps,
        message_history=[],
        model_settings={},
        frontend=frontend,
    )

    assert turn.outcome == "continue"
    status_messages = [msg for kind, msg in frontend.events if kind == "status"]
    assert not any("full" in msg for msg in status_messages)
    assert not any("limit reached" in msg for msg in status_messages)
