"""Tests for LlmSettings provider-specific ModelSettings translation."""

import asyncio

import pytest
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_REASONING_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.call import llm_call
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)
_DEPS = CoDeps(
    shell=ShellBackend(), model=_LLM_MODEL, config=SETTINGS_NO_MCP, session=CoSessionState()
)


@pytest.mark.asyncio
async def test_reasoning_model_settings_drive_real_call():
    """reasoning_model_settings() must produce ModelSettings the configured provider accepts.

    Existing test_flow_llm_call covers the noreason path (llm_call defaults to
    settings_noreason). This test guards the reasoning path — exercises the
    provider-aware dispatch in LlmSettings.reasoning_model_settings() against a
    real provider call.
    """
    settings = SETTINGS_NO_MCP.llm.reasoning_model_settings()
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_REASONING_TIMEOUT_SECS):
        result = await llm_call(
            _DEPS,
            "Reply with the single word: PONG",
            model_settings=settings,
        )
    assert isinstance(result, str)
    assert result.strip()
