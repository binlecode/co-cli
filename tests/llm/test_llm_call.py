"""Behavioral tests for llm_call() that cover real context forwarding and typed output."""

from __future__ import annotations

import asyncio

import pytest
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS as _CONFIG
from tests._settings import TEST_LLM
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

from co_cli.deps import CoDeps
from co_cli.llm.call import llm_call
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_MODEL = build_model(_CONFIG.llm)


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=_CONFIG,
        model=_MODEL,
    )


@pytest.mark.asyncio
async def test_llm_call_with_message_history_forwards_context() -> None:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    deps = _make_deps()
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
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
