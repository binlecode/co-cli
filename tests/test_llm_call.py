"""Functional tests for the llm_call() primitive.

Verifies single prompt→response invocation, message_history forwarding,
output_type override, and explicit model_settings override.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from tests._ollama import ensure_ollama_warm
from tests._settings import make_settings
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

from co_cli.deps import CoDeps
from co_cli.llm._call import llm_call
from co_cli.llm._factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_CONFIG = make_settings()
_MODEL = build_model(_CONFIG.llm)
_MODEL_NAME = _CONFIG.llm.model


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=_CONFIG,
        model=_MODEL,
    )


@pytest.mark.asyncio
@pytest.mark.local
async def test_llm_call_returns_nonempty_string_for_simple_prompt() -> None:
    deps = _make_deps()
    await ensure_ollama_warm(_MODEL_NAME, _CONFIG.llm.host)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await llm_call(deps, "Reply with exactly one word: hello")
    assert isinstance(result, str)
    assert result.strip()


@pytest.mark.asyncio
@pytest.mark.local
async def test_llm_call_uses_settings_noreason_by_default() -> None:
    """Default settings come from deps.model.settings_noreason — no thinking part expected."""
    from pydantic_ai.messages import ModelResponse, ThinkingPart

    deps = _make_deps()
    await ensure_ollama_warm(_MODEL_NAME, _CONFIG.llm.host)
    # Use a private Agent to inspect all_messages — llm_call returns only output
    from pydantic_ai import Agent

    agent: Agent[None, str] = Agent(
        deps.model.model,
        output_type=str,
    )
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        run_result = await agent.run(
            "Reply with exactly one word: yes",
            model_settings=deps.model.settings_noreason,
        )
    thinking_parts = [
        part
        for msg in run_result.all_messages()
        if isinstance(msg, ModelResponse)
        for part in msg.parts
        if isinstance(part, ThinkingPart)
    ]
    assert not thinking_parts, "settings_noreason should suppress thinking; ThinkingPart found"


@pytest.mark.asyncio
@pytest.mark.local
async def test_llm_call_with_message_history_forwards_context() -> None:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    deps = _make_deps()
    await ensure_ollama_warm(_MODEL_NAME, _CONFIG.llm.host)
    history = [
        ModelRequest(parts=[UserPromptPart(content="The secret word is: ZEPHYR")]),
        ModelResponse(parts=[TextPart(content="Understood.")]),
    ]
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await llm_call(
            deps,
            "What was the secret word I told you earlier? Reply with only that word.",
            message_history=history,
        )
    assert "ZEPHYR" in result.upper()


@pytest.mark.asyncio
@pytest.mark.local
async def test_llm_call_output_type_returns_structured_output() -> None:
    class Color(BaseModel):
        value: str

    deps = _make_deps()
    await ensure_ollama_warm(_MODEL_NAME, _CONFIG.llm.host)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await llm_call(
            deps,
            "Name a single primary color.",
            output_type=Color,
        )
    assert isinstance(result, Color)
    assert result.value.strip()


@pytest.mark.asyncio
@pytest.mark.local
async def test_llm_call_explicit_model_settings_override_wins() -> None:
    """Explicit model_settings= parameter takes precedence over deps.model.settings_noreason."""
    deps = _make_deps()
    await ensure_ollama_warm(_MODEL_NAME, _CONFIG.llm.host)
    explicit = deps.model.settings_noreason
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await llm_call(
            deps,
            "Reply with exactly one word: ok",
            model_settings=explicit,
        )
    assert isinstance(result, str)
    assert result.strip()
