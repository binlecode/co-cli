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

from co_cli.config._llm import LlmSettings
from co_cli.prompts.model_quirks._loader import get_model_inference, normalize_model_name

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
    context_window: int | None = None


def build_model(llm: LlmSettings) -> LlmModel:
    """Build a pydantic-ai model object from LlmSettings.

    Reads model quirks for provider-specific inference defaults.

    ollama-openai  → OpenAIChatModel wrapping the Ollama OpenAI-compatible API
    gemini         → GoogleModel with GoogleProvider (api_key injected via constructor)

    Returns an LlmModel with the model object, base ModelSettings from quirks,
    and context_window from model quirks (None when not declared).

    Raises ValueError for unsupported providers.
    """
    model_name = llm.model
    normalized = normalize_model_name(model_name)

    if llm.uses_ollama_openai():
        inf = get_model_inference("ollama-openai", normalized)
        extra: dict[str, Any] = {}
        num_ctx = inf.get("num_ctx")
        if num_ctx is not None:
            extra["num_ctx"] = num_ctx
        extra.update(inf.get("extra_body", {}))
        model_settings = ModelSettings(
            temperature=inf.get("temperature", 0.7),
            top_p=inf.get("top_p", 1.0),
            max_tokens=inf.get("max_tokens", 16384),
            extra_body=extra,
        )
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
            model=model, settings=model_settings, context_window=inf.get("context_window")
        )

    if llm.uses_gemini():
        inf = get_model_inference("gemini", normalized)
        model_settings = ModelSettings(
            temperature=inf.get("temperature", 1.0),
            top_p=inf.get("top_p", 0.95),
            max_tokens=inf.get("max_tokens", 65536),
        )
        google_model = GoogleModel(model_name, provider=GoogleProvider(api_key=llm.api_key))
        return LlmModel(
            model=google_model, settings=model_settings, context_window=inf.get("context_window")
        )

    raise ValueError(f"Unsupported provider: {llm.provider!r}")
