"""Functional tests for OllamaNativeModel that do not require a live Ollama instance.

Tests requiring a running Ollama instance live in evals/eval_ollama_native.py.
"""

import asyncio

import pytest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.settings import ModelSettings

from co_cli._model_factory import OllamaNativeModel, ResolvedModel
from co_cli.config import DEFAULT_OLLAMA_SUMMARIZATION_MODEL
from co_cli.context._history import _run_summarization_with_policy


_THINK_MODEL = DEFAULT_OLLAMA_SUMMARIZATION_MODEL["model"]


@pytest.mark.asyncio
async def test_run_summarization_returns_none_on_unreachable_host() -> None:
    """_run_summarization_with_policy returns None when the model host is unreachable.

    Validates that network errors exhaust retries and return None rather than raising.
    """
    broken = ResolvedModel(
        model=OllamaNativeModel(
            model_name=_THINK_MODEL,
            llm_host="http://localhost:19999",
            settings=ModelSettings(temperature=0.1, max_tokens=64),
            think=False,
        ),
        settings=ModelSettings(temperature=0.1, max_tokens=64),
    )
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="What is Docker?")]),
        ModelResponse(parts=[TextPart(content="Docker is a container platform.")]),
    ]

    async with asyncio.timeout(30):
        result = await _run_summarization_with_policy(msgs, broken, max_retries=1)

    assert result is None, (
        f"Expected None when host is unreachable (retries exhausted), got: {result!r}"
    )
