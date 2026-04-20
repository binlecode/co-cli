"""Functional tests for ThinkingPart extraction from Ollama responses.

Two paths tested:
- Reasoning enabled (no reasoning_effort override): ThinkingPart present in response.
- Noreason settings (reasoning_effort=none): ThinkingPart absent (control).
"""

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ThinkingPart
from tests._ollama import ensure_ollama_warm
from tests._settings import make_settings
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS, LLM_REASONING_TIMEOUT_SECS

from co_cli.llm._factory import build_model

_CONFIG = make_settings()
_MODEL = build_model(_CONFIG.llm)
_REASON_SETTINGS = _CONFIG.llm.reasoning_model_settings()
_NOREASON_SETTINGS = _CONFIG.llm.noreason_model_settings()
_MODEL_NAME = _CONFIG.llm.model


@pytest.mark.asyncio
@pytest.mark.local
async def test_thinking_part_present_when_reasoning_enabled():
    """ThinkingPart appears in pydantic-ai response when reasoning model settings are used."""
    agent = Agent(_MODEL.model, output_type=str)
    await ensure_ollama_warm(_MODEL_NAME, _CONFIG.llm.host)
    async with asyncio.timeout(LLM_REASONING_TIMEOUT_SECS):
        result = await agent.run(
            "Reply with exactly one word: yes", model_settings=_REASON_SETTINGS
        )
    thinking_parts = [
        part
        for msg in result.all_messages()
        if isinstance(msg, ModelResponse)
        for part in msg.parts
        if isinstance(part, ThinkingPart)
    ]
    assert thinking_parts, "Expected ThinkingPart in response when reasoning is enabled"
    assert any(len(p.content) > 0 for p in thinking_parts), (
        "ThinkingPart content must be non-empty"
    )


@pytest.mark.asyncio
@pytest.mark.local
async def test_thinking_part_absent_when_reasoning_disabled():
    """No ThinkingPart in response when reasoning_effort=none is sent (control)."""
    agent = Agent(_MODEL.model, output_type=str)
    await ensure_ollama_warm(_MODEL_NAME, _CONFIG.llm.host)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await agent.run(
            "Reply with exactly one word: yes", model_settings=_NOREASON_SETTINGS
        )
    thinking_parts = [
        part
        for msg in result.all_messages()
        if isinstance(msg, ModelResponse)
        for part in msg.parts
        if isinstance(part, ThinkingPart)
    ]
    assert not thinking_parts, (
        "Expected no ThinkingPart when reasoning_effort=none is sent to Ollama"
    )
