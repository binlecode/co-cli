"""Tests for the non-agent LLM call primitive — noreason and reasoning paths."""

import asyncio
import json
import logging
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS, LLM_REASONING_TIMEOUT_SECS

from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.call import llm_call
from co_cli.llm.factory import build_model
from co_cli.observability import tracing
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)
_DEPS = CoDeps(
    shell=ShellBackend(), model=_LLM_MODEL, config=SETTINGS_NO_MCP, session=CoSessionState()
)


@pytest.mark.asyncio
async def test_llm_call_returns_non_empty_text():
    """llm_call must return a non-empty string from a direct model request."""
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await llm_call(_DEPS, "Reply with the single word: PONG")
    assert "PONG" in result.upper()


@pytest.mark.asyncio
async def test_llm_call_respects_system_instructions():
    """llm_call must inject the instructions parameter as a system prompt."""
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await llm_call(
            _DEPS,
            "What is the capital of France?",
            instructions="You must respond with only the single word 'PARIS' and nothing else.",
        )
    assert "PARIS" in result.upper()


@pytest.mark.asyncio
async def test_llm_call_threads_message_history():
    """llm_call must make prior message_history visible to the model."""
    history = [
        ModelRequest(parts=[UserPromptPart(content="My secret code word is ZEPHYR.")]),
        ModelResponse(parts=[TextPart(content="Understood, your code word is ZEPHYR.")]),
    ]
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await llm_call(
            _DEPS,
            "What is the code word I told you earlier?",
            message_history=history,
        )
    assert "ZEPHYR" in result.upper()


@pytest.mark.asyncio
async def test_reasoning_model_settings_drive_real_call():
    """reasoning_model_settings() must produce ModelSettings the configured provider accepts.

    Tests above cover the noreason path (llm_call defaults to settings_noreason). This
    test guards the reasoning path — exercises the provider-aware dispatch in
    LlmSettings.reasoning_model_settings() against a real provider call.
    """
    settings = SETTINGS_NO_MCP.llm.reasoning_model_settings()
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_REASONING_TIMEOUT_SECS):
        result = await llm_call(
            _DEPS,
            "Reply with the single word: PONG",
            model_settings=settings,
        )
    assert "PONG" in result.upper()


@pytest.fixture
def isolated_spans_log(tmp_path: Path):
    """Isolated spans log with clean state; restores logger state on teardown."""
    logger = logging.getLogger("co_cli.observability.spans")
    saved_handlers = list(logger.handlers)
    saved_patterns = list(tracing._COMPILED_PATTERNS)
    for h in saved_handlers:
        logger.removeHandler(h)
    tracing._SPAN_STACK.set(())

    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)
    yield log

    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    for h in saved_handlers:
        logger.addHandler(h)
    tracing._COMPILED_PATTERNS = saved_patterns


@pytest.mark.asyncio
async def test_llm_call_emits_model_span(isolated_spans_log: Path):
    """llm_call must emit one ``llm_call <model>`` span carrying model-level cost
    attributes at parity with the agent-path ``chat`` span (BC-1)."""
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        await llm_call(_DEPS, "Reply with the single word: PONG")

    records = [
        json.loads(line) for line in isolated_spans_log.read_text().splitlines() if line.strip()
    ]
    model_name = _LLM_MODEL.model.model_name
    spans = [r for r in records if r["name"] == f"llm_call {model_name}"]
    assert len(spans) == 1
    attrs = spans[0]["attributes"]
    assert attrs["co.model.tokens.output"] is not None
    assert attrs["co.model.finish_reason"] is not None
