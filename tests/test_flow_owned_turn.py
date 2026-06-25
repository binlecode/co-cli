"""Owned-loop orchestrator turn tests.

A no-LLM behavioral pin on the reasoning-overflow predicate plus real-Ollama end-to-end
turns through ``run_turn_owned`` on read-only tools — the load-bearing Phase-2 gate that the
owned path reaches the graph path's behavior on the no-approval / no-recovery slice. Real-LLM
tests skip unless Ollama is configured; the model is warmed outside the call timeout.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
from co_cli.agent.loop import _is_reasoning_overflow, run_turn_owned
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_NATIVE, _CATALOG = build_native_toolset()


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=build_model(_CONFIG_NO_MCP.llm),
        toolset=assemble_routing_toolset(_NATIVE, []),
        tool_catalog=_CATALOG,
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
    )


# ---------------------------------------------------------------------------
# Reasoning-overflow predicate (no LLM) — typed replacement for the string match.
# ---------------------------------------------------------------------------


def test_reasoning_overflow_when_length_and_no_answer_content() -> None:
    assert _is_reasoning_overflow(ModelResponse(parts=[], finish_reason="length")) is True
    thinking_only = ModelResponse(parts=[ThinkingPart(content="...")], finish_reason="length")
    assert _is_reasoning_overflow(thinking_only) is True


def test_not_reasoning_overflow_when_text_present_or_not_length() -> None:
    text_len = ModelResponse(parts=[TextPart(content="hi")], finish_reason="length")
    assert _is_reasoning_overflow(text_len) is False
    text_stop = ModelResponse(parts=[TextPart(content="hi")], finish_reason="stop")
    assert _is_reasoning_overflow(text_stop) is False


# ---------------------------------------------------------------------------
# Real-Ollama end-to-end owned turns (read-only tools, no approval, no recovery).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM owned turn needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_plain_chat_turn_streams_and_answers() -> None:
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    deps = _make_deps()
    frontend = HeadlessFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        turn = await run_turn_owned(
            user_input="Reply with a one-sentence greeting. Do not call any tools.",
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert turn.outcome == "continue"
    assert isinstance(turn.output, str)
    assert turn.output.strip()
    assert turn.model_requests >= 1


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM owned turn needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_single_tool_call_turn_answers_from_result() -> None:
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    deps = _make_deps()
    frontend = HeadlessFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        turn = await run_turn_owned(
            user_input=(
                "Call the capabilities_check tool now to see what you can do, then tell me "
                "one capability you have in a single sentence."
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert turn.outcome == "continue"
    assert isinstance(turn.output, str)
    assert turn.output.strip()
    # tool call + answer = at least two model requests, and the turn terminates on no-tool-calls.
    assert turn.model_requests >= 2
