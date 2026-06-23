"""Tests for reasoning-token-overflow handling in run_turn().

When a reasoning model spends its entire output budget on thinking and produces
no answer, pydantic-ai raises UnexpectedModelBehavior with a "exceeded before any
response was generated" message. run_turn must surface a named, actionable status
(not the generic "malformed output") and end the turn as an error — without
persisting any empty/thinking-only assistant turn into history.

Production paths:
  co_cli/agent/orchestrate.py — _REASONING_OVERFLOW_SIGNATURE/_MESSAGE, run_turn
                                 UnexpectedModelBehavior handler
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCalls, FunctionModel
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.orchestrate import run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.tools.shell_backend import ShellBackend


def _make_deps() -> CoDeps:
    """Return plain CoDeps for a single-turn run (no cap overrides)."""
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )


def _make_raising_agent(message: str) -> Agent:
    """Agent whose model raises UnexpectedModelBehavior(message) before any output."""

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        raise UnexpectedModelBehavior(message)
        yield  # unreachable; marks this as an async generator

    return Agent(
        FunctionModel(stream_function=stream_fn),
        deps_type=CoDeps,
        output_type=[str, DeferredToolRequests],
    )


def _has_textless_response(messages: list[ModelMessage]) -> bool:
    """True if any assistant ModelResponse carries no TextPart (a poisoned turn)."""
    return any(
        isinstance(msg, ModelResponse) and not any(isinstance(p, TextPart) for p in msg.parts)
        for msg in messages
    )


@pytest.mark.asyncio
async def test_reasoning_overflow_surfaces_actionable_status() -> None:
    """Overflow signature must yield the actionable status and an error turn,
    and must never persist an empty/thinking-only assistant turn."""
    overflow = (
        "Model token limit (8192) exceeded before any response was generated. "
        "Increase the `max_tokens` model setting, or simplify the prompt..."
    )
    deps = _make_deps()
    agent = _make_raising_agent(overflow)
    frontend = HeadlessFrontend()

    turn = await run_turn(
        agent=agent,
        user_input="explain something hard",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "error", f"expected error outcome; got {turn.outcome!r}"
    assert any("entire output budget" in s for s in frontend.statuses), (
        f"status must name the reasoning overflow; got statuses: {frontend.statuses}"
    )
    assert not any("malformed output" in s for s in frontend.statuses), (
        f"reasoning overflow must not use the generic malformed message; got: {frontend.statuses}"
    )
    assert not _has_textless_response(turn.messages), (
        "no empty/thinking-only assistant turn may persist into history"
    )


@pytest.mark.asyncio
async def test_other_unexpected_behavior_keeps_generic_message() -> None:
    """A non-overflow UnexpectedModelBehavior must still use the generic malformed message."""
    deps = _make_deps()
    agent = _make_raising_agent("Exceeded the maximum number of retries trying to parse output")
    frontend = HeadlessFrontend()

    turn = await run_turn(
        agent=agent,
        user_input="do a thing",
        deps=deps,
        message_history=[],
        frontend=frontend,
    )

    assert turn.outcome == "error", f"expected error outcome; got {turn.outcome!r}"
    assert any("malformed output" in s for s in frontend.statuses), (
        f"non-overflow case must use the generic malformed message; got: {frontend.statuses}"
    )
    assert not any("entire output budget" in s for s in frontend.statuses), (
        f"non-overflow case must not use the reasoning-overflow message; got: {frontend.statuses}"
    )
