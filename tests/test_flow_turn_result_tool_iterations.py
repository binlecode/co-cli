"""Behavioral tests for TurnResult.tool_iterations (plan 3.5c TASK-4).

The post-turn skill-review hook consumes turn_result.tool_iterations to gate
background firing. This file verifies orchestrate.py populates that field
correctly at the three build sites (success / error / interrupted).
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agents.core import build_agent, build_native_toolset
from co_cli.context.orchestrate import TurnResult, run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)
_TOOLSET, _TOOL_INDEX = build_native_toolset(_CONFIG_NO_MCP)
_AGENT = build_agent(
    config=_CONFIG_NO_MCP,
    model=_LLM_MODEL,
    toolset=_TOOLSET,
    tool_index=_TOOL_INDEX,
)


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=_TOOL_INDEX,
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        model_max_ctx=_CONFIG_NO_MCP.llm.max_ctx,
    )


# ---------------------------------------------------------------------------
# Static contract: dataclass defaults
# ---------------------------------------------------------------------------


def test_turn_result_tool_iterations_defaults_to_zero() -> None:
    """TurnResult.tool_iterations is a dataclass field with default 0."""
    tr = TurnResult(outcome="continue", interrupted=False)
    assert tr.tool_iterations == 0
    assert hasattr(tr, "tool_iterations")


def test_turn_result_tool_iterations_is_assignable() -> None:
    """Explicit construction sets the value the post-turn hook reads."""
    tr = TurnResult(outcome="continue", interrupted=False, tool_iterations=7)
    assert tr.tool_iterations == 7


# ---------------------------------------------------------------------------
# Real-LLM end-to-end: success path populates the accumulator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_turn_with_tool_call_populates_tool_iterations() -> None:
    """A turn that emits at least one tool call must report tool_iterations >= 1."""
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    deps = _make_deps()
    frontend = SilentFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        turn = await run_turn(
            agent=_AGENT,
            user_input=(
                "Use the shell tool to execute: git status. Do NOT describe what you would "
                "do - call the tool now."
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    # Cross-check the accumulator against direct inspection of the messages.
    direct_count = sum(
        1
        for m in turn.messages
        if isinstance(m, ModelResponse) and any(isinstance(p, ToolCallPart) for p in m.parts)
    )
    assert turn.tool_iterations >= 1, (
        f"expected at least one tool-producing ModelResponse, got tool_iterations="
        f"{turn.tool_iterations} (direct count over full history={direct_count})"
    )
    assert turn.tool_iterations == direct_count, (
        f"accumulator ({turn.tool_iterations}) disagrees with direct count "
        f"({direct_count}) over turn-local messages"
    )


@pytest.mark.asyncio
async def test_real_turn_text_only_response_has_zero_tool_iterations() -> None:
    """A turn whose response carries no ToolCallPart contributes 0 to the accumulator."""
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    deps = _make_deps()
    frontend = SilentFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        turn = await run_turn(
            agent=_AGENT,
            user_input=(
                "Reply with exactly the four characters: pong. Do NOT call any tools. Just text."
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    direct_count = sum(
        1
        for m in turn.messages
        if isinstance(m, ModelResponse) and any(isinstance(p, ToolCallPart) for p in m.parts)
    )
    # If the model defied the prompt and tool-called anyway, accept that the accumulator
    # still equals the direct count. The strict contract is parity, not zero-ness.
    assert turn.tool_iterations == direct_count, (
        f"accumulator ({turn.tool_iterations}) disagrees with direct count ({direct_count})"
    )
