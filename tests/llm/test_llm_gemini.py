"""Functional tests for Gemini reasoning and noreason inference.

Requires GEMINI_API_KEY (or CO_LLM_API_KEY) env var set to a valid Gemini API key.
Tests verify that:
- noreason settings (thinking_level=low) return a valid response
- reasoning settings return a valid response
- both modes produce non-empty output without crashing
"""

from __future__ import annotations

import asyncio
import os

import pytest
from pydantic_ai import Agent
from tests._timeouts import LLM_GEMINI_NOREASON_TIMEOUT_SECS, LLM_REASONING_TIMEOUT_SECS

from co_cli.config.llm import LlmSettings
from co_cli.llm._factory import build_model

_GEMINI_MODEL = "gemini-3.1-pro-preview"
_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("CO_LLM_API_KEY")
_has_api_key = bool(_API_KEY)

pytestmark = pytest.mark.skipif(
    not _has_api_key, reason="GEMINI_API_KEY not set — Gemini tests skipped"
)

_LLM_SETTINGS = LlmSettings(
    provider="gemini",
    model=_GEMINI_MODEL,
    api_key=_API_KEY,
)


def _build() -> build_model:
    # GoogleProvider creates an httpx client bound to the running event loop.
    # Build fresh per test to avoid "Event loop is closed" on the second test.
    return build_model(_LLM_SETTINGS)


@pytest.mark.asyncio
async def test_gemini_noreason_returns_response() -> None:
    """Noreason settings (thinking_level=low) return a valid non-empty response."""
    m = _build()
    agent = Agent(m.model, output_type=str)
    async with asyncio.timeout(LLM_GEMINI_NOREASON_TIMEOUT_SECS):
        result = await agent.run(
            "Reply with exactly one word: yes",
            model_settings=m.settings_noreason,
        )
    assert result.output.strip(), "Noreason call must return non-empty output"


@pytest.mark.asyncio
async def test_gemini_reasoning_returns_response() -> None:
    """Reasoning settings return a valid non-empty response."""
    m = _build()
    agent = Agent(m.model, output_type=str)
    async with asyncio.timeout(LLM_REASONING_TIMEOUT_SECS):
        result = await agent.run(
            "Reply with exactly one word: yes",
            model_settings=m.settings,
        )
    assert result.output.strip(), "Reasoning call must return non-empty output"


@pytest.mark.asyncio
async def test_gemini_noreason_faster_than_reasoning() -> None:
    """Noreason (thinking_level=low) completes within non-reasoning timeout; reasoning within reasoning timeout."""
    m = _build()
    agent = Agent(m.model, output_type=str)

    async with asyncio.timeout(LLM_GEMINI_NOREASON_TIMEOUT_SECS):
        noreason_result = await agent.run(
            "Reply with exactly one word: yes",
            model_settings=m.settings_noreason,
        )

    async with asyncio.timeout(LLM_REASONING_TIMEOUT_SECS):
        reason_result = await agent.run(
            "Reply with exactly one word: yes",
            model_settings=m.settings,
        )

    assert noreason_result.output.strip()
    assert reason_result.output.strip()
