"""Evals for OllamaNativeModel — requires a running Ollama instance.

Runs against the real configured system. Skip gracefully if Ollama provider is not configured.

Tests:
- think=False suppresses reasoning chain and produces non-empty content via /api/chat
- Multi-turn conversation context is preserved
- Model registry builds OllamaNativeModel for the summarization role
"""

import asyncio
from pathlib import Path

import pytest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from co_cli._model_factory import OllamaNativeModel, ModelRegistry, ResolvedModel
from co_cli.config import DEFAULT_OLLAMA_SUMMARIZATION_MODEL, settings as _settings
from co_cli.deps import CoConfig
from tests._ollama import ensure_ollama_warm


_CONFIG = CoConfig.from_settings(_settings, cwd=Path.cwd())
_THINK_MODEL = DEFAULT_OLLAMA_SUMMARIZATION_MODEL["model"]

_OLLAMA_CONFIGURED = _CONFIG.llm_provider in ("ollama-openai", "ollama-native")


def test_model_registry_summarization_uses_native_model() -> None:
    """The summarization model is OllamaNativeModel for ollama provider.

    Validates the full config → ModelEntry → build_model → OllamaNativeModel path.
    If this breaks, summarization silently falls back to the OpenAI-compat layer
    where think=False is broken and content is always empty.
    """
    if not _OLLAMA_CONFIGURED:
        print("SKIP: Ollama provider not configured")
        return
    registry = ModelRegistry.from_config(_CONFIG)
    fallback = ResolvedModel(model=None, settings=None)
    resolved = registry.get("summarization", fallback)
    assert isinstance(resolved.model, OllamaNativeModel), (
        f"Summarization model must be OllamaNativeModel, got {type(resolved.model).__name__}. "
        "Check DEFAULT_OLLAMA_SUMMARIZATION_MODEL['provider'] == 'ollama-native'."
    )
    assert resolved.model._think is False, (
        "Summarization model must have think=False to suppress reasoning chain"
    )


@pytest.mark.asyncio
async def test_ollama_native_think_false_produces_content() -> None:
    """OllamaNativeModel with think=False returns non-empty text content.

    Validates the core requirement: think model with think=False via /api/chat
    produces actual text — not empty content with reasoning trapped in ThinkingPart
    (which is what the OpenAI-compat /v1/chat/completions wrapper produces for qwen3.5).

    Regression guard: if Ollama's /api/chat response format changes so that
    content is empty, summarization silently produces empty summaries.
    """
    if not _OLLAMA_CONFIGURED:
        print("SKIP: Ollama provider not configured")
        return

    await ensure_ollama_warm(_THINK_MODEL, _CONFIG.llm_host)

    model = OllamaNativeModel(
        model_name=_THINK_MODEL,
        llm_host=_CONFIG.llm_host,
        settings=ModelSettings(temperature=0.1, max_tokens=64),
        think=False,
    )
    msgs = [ModelRequest(parts=[UserPromptPart(content="Reply with exactly: hello")])]
    async with asyncio.timeout(120):
        response = await model.request(msgs, None, ModelRequestParameters())

    assert isinstance(response, ModelResponse)
    text_parts = [p for p in response.parts if isinstance(p, TextPart)]
    assert text_parts, "Expected at least one TextPart — got empty response"
    assert text_parts[0].content.strip(), (
        f"Content is empty — think=False via /api/chat is broken: {response.parts!r}"
    )


@pytest.mark.asyncio
async def test_ollama_native_multi_turn_conversation() -> None:
    """OllamaNativeModel preserves multi-turn context in /api/chat history.

    The model must answer the second question using information from the first turn.
    Validates that _messages_to_ollama correctly assembles the messages array.
    """
    if not _OLLAMA_CONFIGURED:
        print("SKIP: Ollama provider not configured")
        return

    await ensure_ollama_warm(_THINK_MODEL, _CONFIG.llm_host)

    model = OllamaNativeModel(
        model_name=_THINK_MODEL,
        llm_host=_CONFIG.llm_host,
        settings=ModelSettings(temperature=0.1, max_tokens=64),
        think=False,
    )
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="My name is TestUser.")]),
        ModelResponse(parts=[TextPart(content="Hello TestUser!")]),
        ModelRequest(parts=[UserPromptPart(content="What is my name? Reply in one word.")]),
    ]
    async with asyncio.timeout(120):
        response = await model.request(msgs, None, ModelRequestParameters())

    text_parts = [p for p in response.parts if isinstance(p, TextPart)]
    assert text_parts, "No TextPart in response"
    assert "testuser" in text_parts[0].content.lower(), (
        f"Expected 'testuser' in response (multi-turn context lost): {text_parts[0].content!r}"
    )
