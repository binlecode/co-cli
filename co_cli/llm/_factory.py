"""Provider-aware model factory — builds an LLM model object from LlmSettings."""

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from co_cli.config._llm import LlmSettings, resolve_reasoning_inference

logger = logging.getLogger(__name__)

_HTTP_CONNECT_TIMEOUT = 10.0
_HTTP_READ_TIMEOUT = 300.0
_HTTP_WRITE_TIMEOUT = 30.0
_HTTP_POOL_TIMEOUT = 10.0


@dataclass
class LlmModel:
    """A pre-built model object paired with its inference settings.

    Read-only container stored on ``CoDeps.model``. Not passed as a parameter
    to functions — callers access ``.model`` and ``.settings`` separately.
    """

    model: Any
    settings: ModelSettings | None
    settings_noreason: ModelSettings | None = None
    context_window: int | None = None


def build_model(llm: LlmSettings) -> LlmModel:
    """Build a pydantic-ai model object from LlmSettings.

    Resolves provider/model-specific inference defaults from config/runtime code.

    ollama-openai  → OpenAIChatModel wrapping the Ollama OpenAI-compatible API
    gemini         → GoogleModel with GoogleProvider (api_key injected via constructor)

    Returns an LlmModel with the model object, base ModelSettings from llm settings,
    and context_window from resolved model defaults (None when not declared).

    Raises ValueError for unsupported providers.
    """
    model_name = llm.model
    inference = resolve_reasoning_inference(llm)

    if llm.uses_ollama_openai():
        model_settings = llm.reasoning_model_settings()
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_HTTP_CONNECT_TIMEOUT,
                read=_HTTP_READ_TIMEOUT,
                write=_HTTP_WRITE_TIMEOUT,
                pool=_HTTP_POOL_TIMEOUT,
            )
        )
        _openai_client = AsyncOpenAI(
            base_url=f"{llm.host}/v1",
            api_key="ollama",
            http_client=_http_client,
        )
        model = OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(openai_client=_openai_client),
        )
        return LlmModel(
            model=model,
            settings=model_settings,
            settings_noreason=llm.noreason_model_settings(),
            context_window=inference.get("context_window"),
        )

    if llm.uses_gemini():
        model_settings = llm.reasoning_model_settings()
        google_model = GoogleModel(model_name, provider=GoogleProvider(api_key=llm.api_key))
        return LlmModel(
            model=google_model,
            settings=model_settings,
            settings_noreason=llm.noreason_model_settings(),
            context_window=inference.get("context_window"),
        )

    raise ValueError(f"Unsupported provider: {llm.provider!r}")
