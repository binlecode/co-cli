"""Functional tests for Gemini reasoning and noreason inference.

Requires GEMINI_API_KEY (or CO_LLM_API_KEY) env var set to a valid Gemini API key.
Tests verify that:
- noreason settings (thinking_level=MINIMAL) return a valid response
- reasoning settings return a valid response
- both modes produce non-empty output without crashing
"""

from __future__ import annotations

import asyncio
import os

import pytest
from pydantic_ai import Agent
from tests._timeouts import LLM_GEMINI_NOREASON_TIMEOUT_SECS, LLM_REASONING_TIMEOUT_SECS

from co_cli.config.llm import DEFAULT_GEMINI_MODEL, LlmSettings
from co_cli.llm.factory import build_model

_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("CO_LLM_API_KEY")

_LLM_SETTINGS = LlmSettings(
    provider="gemini",
    model=DEFAULT_GEMINI_MODEL,
    api_key=_API_KEY,
)


def _build() -> build_model:
    # GoogleProvider creates an httpx client bound to the running event loop.
    # Build fresh per test to avoid "Event loop is closed" on the second test.
    return build_model(_LLM_SETTINGS)


@pytest.mark.skipif(not _API_KEY, reason="GEMINI_API_KEY or CO_LLM_API_KEY required")
@pytest.mark.asyncio
async def test_gemini_noreason_returns_response() -> None:
    """Noreason settings (thinking_level=MINIMAL) return a valid non-empty response."""
    m = _build()
    agent = Agent(m.model, output_type=str, model_settings=m.settings_noreason)
    async with asyncio.timeout(LLM_GEMINI_NOREASON_TIMEOUT_SECS):
        result = await agent.run("Reply with exactly one word: yes")
    assert result.output.strip(), "Noreason call must return non-empty output"


@pytest.mark.skipif(not _API_KEY, reason="GEMINI_API_KEY or CO_LLM_API_KEY required")
@pytest.mark.asyncio
async def test_gemini_reasoning_returns_response() -> None:
    """Reasoning settings return a valid non-empty response."""
    m = _build()
    agent = Agent(m.model, output_type=str, model_settings=m.settings)
    async with asyncio.timeout(LLM_REASONING_TIMEOUT_SECS):
        result = await agent.run("Reply with exactly one word: yes")
    assert result.output.strip(), "Reasoning call must return non-empty output"
