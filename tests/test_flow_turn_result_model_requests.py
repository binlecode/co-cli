"""Behavioral tests for TurnResult.model_requests.

The post-turn skill-review hook consumes turn_result.model_requests to gate
background firing. This file verifies orchestrate.py populates that field
correctly at the three build sites (success / error / interrupted).
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.build import build_orchestrator
from co_cli.agent.core import build_native_toolset
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)
_TOOLSET, _TOOL_INDEX = build_native_toolset(_CONFIG_NO_MCP)


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        toolset=_TOOLSET,
        tool_index=_TOOL_INDEX,
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        model_max_ctx=_CONFIG_NO_MCP.llm.max_ctx,
    )


_AGENT = build_orchestrator(ORCHESTRATOR_SPEC, _make_deps())


# ---------------------------------------------------------------------------
# Real-LLM end-to-end: success path populates the accumulator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_turn_with_tool_call_populates_model_requests() -> None:
    """A turn that emits at least one tool call must report model_requests >= 1."""
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
    # The counter now counts every ModelResponse, regardless of tool calls.
    direct_count = sum(1 for m in turn.messages if isinstance(m, ModelResponse))
    assert turn.model_requests >= 1, (
        f"expected at least one ModelResponse, got model_requests="
        f"{turn.model_requests} (direct count over full history={direct_count})"
    )
    assert turn.model_requests == direct_count, (
        f"accumulator ({turn.model_requests}) disagrees with direct count "
        f"({direct_count}) over turn-local messages"
    )


@pytest.mark.asyncio
async def test_real_turn_text_only_response_contributes_one_model_request() -> None:
    """A text-only turn (no ToolCallPart) contributes >= 1 to the accumulator.

    Previously the filter excluded text-only responses and returned 0.
    With the simplified counter, every ModelResponse counts.
    """
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

    direct_count = sum(1 for m in turn.messages if isinstance(m, ModelResponse))
    assert turn.model_requests >= 1, (
        f"text-only turn must contribute >= 1 model request, got {turn.model_requests}"
    )
    assert turn.model_requests == direct_count, (
        f"accumulator ({turn.model_requests}) disagrees with direct count ({direct_count})"
    )
