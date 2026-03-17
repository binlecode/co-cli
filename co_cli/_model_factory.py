"""Provider-aware model factory — builds and caches LLM model objects by role."""

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
import ollama
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings, merge_model_settings
from pydantic_ai.usage import RequestUsage

from co_cli.config import ModelEntry
from co_cli.prompts.model_quirks._loader import get_model_inference, normalize_model_name

logger = logging.getLogger(__name__)

_HTTP_CONNECT_TIMEOUT = 10.0
_HTTP_READ_TIMEOUT = 300.0
_HTTP_WRITE_TIMEOUT = 30.0
_HTTP_POOL_TIMEOUT = 10.0


def prepare_provider(provider: str, llm_api_key: str | None) -> None:
    """Configure provider-level prerequisites before building models.

    Side effects are intentional and visible at call sites in agent.py.
    Currently handles Gemini API key injection into the environment
    (pydantic-ai reads GEMINI_API_KEY at model instantiation time).
    """
    if provider == "gemini":
        if not llm_api_key:
            raise ValueError("llm_api_key is required in settings when llm_provider is 'gemini'.")
        os.environ["GEMINI_API_KEY"] = llm_api_key


@dataclass
class ResolvedModel:
    """A pre-built model object paired with its inference settings."""

    model: Any
    settings: ModelSettings | None


class ModelRegistry:
    """Session-scoped registry of pre-built ResolvedModel objects keyed by role.

    Built once from config at session start, stored in CoServices, and provides
    a pre-built ResolvedModel for any in-session component by role lookup.
    One model per role.
    """

    def __init__(self) -> None:
        self._models: dict[str, ResolvedModel] = {}

    @classmethod
    def from_config(cls, config: Any) -> "ModelRegistry":
        """Build the registry from a CoConfig-compatible object.

        Accepts config as untyped to avoid importing CoConfig (no circular import
        risk but keeps _model_factory.py dep-free of deps.py).
        """
        registry = cls()
        prepare_provider(config.llm_provider, config.llm_api_key)
        for role, entry in config.role_models.items():
            if not entry:
                continue
            model, settings = build_model(entry, config.llm_provider, config.llm_host)
            registry._models[role] = ResolvedModel(model=model, settings=settings)
        return registry

    def get(self, role: str, fallback: ResolvedModel) -> ResolvedModel:
        """Return the ResolvedModel for role, or fallback if unconfigured."""
        return self._models.get(role, fallback)

    def is_configured(self, role: str) -> bool:
        """Return True if role has a registered ResolvedModel."""
        return role in self._models


class OllamaNativeModel(Model):
    """Pydantic-ai Model implementation using Ollama's native /api/chat endpoint.

    Unlike the OpenAI-compatible /v1/chat/completions wrapper, the native endpoint
    exposes top-level fields such as `think` (bool) for controlling reasoning chains
    at call-time — allowing the think model to produce plain text output without the
    VRAM cost of loading a separate instruct variant.

    Only implements non-streaming `request()`. Streaming is not needed for the
    summarization pipeline that uses this model.
    """

    def __init__(
        self,
        model_name: str,
        llm_host: str,
        settings: ModelSettings | None = None,
        # think=None means: don't send the field (let model default)
        think: bool | None = None,
    ) -> None:
        super().__init__(settings=settings)
        self._model_name = model_name
        self._llm_host = llm_host
        self._think = think

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def system(self) -> str:
        return "ollama"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Convert pydantic-ai messages to Ollama chat format and call the API."""
        merged = merge_model_settings(self.settings, model_settings)

        ollama_messages = _messages_to_ollama(messages)

        options: dict[str, Any] = {}
        if merged:
            if merged.get("temperature") is not None:
                options["temperature"] = merged["temperature"]
            if merged.get("top_p") is not None:
                options["top_p"] = merged["top_p"]
            if merged.get("max_tokens") is not None:
                # Ollama uses num_predict for max generation tokens
                options["num_predict"] = merged["max_tokens"]
            for k, v in (merged.get("extra_body") or {}).items():
                if k == "think":
                    # think is a top-level chat field, not an option
                    continue
                options[k] = v

        _ollama = ollama.AsyncClient(host=self._llm_host)
        try:
            resp = await _ollama.chat(
                model=self._model_name,
                messages=ollama_messages,
                stream=False,
                think=self._think,
                options=options or None,
            )
        except ollama.ResponseError as exc:
            raise ModelHTTPError(
                status_code=exc.status_code,
                model_name=self._model_name,
                body=str(exc.error),
            ) from exc
        except Exception as exc:
            raise ModelAPIError(
                model_name=self._model_name,
                message=f"Ollama chat request failed: {exc}",
            ) from exc
        finally:
            # TODO: replace with public API when ollama-py adds async context manager support (v0.6.1 lacks it)
            await _ollama._client.aclose()

        content = (resp.message.content or "") if resp.message else ""
        usage = RequestUsage(
            input_tokens=resp.prompt_eval_count or 0,
            output_tokens=resp.eval_count or 0,
        )
        return ModelResponse(parts=[TextPart(content=content)], usage=usage)


def _messages_to_ollama(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """Convert pydantic-ai message history to Ollama /api/chat messages array.

    Handles:
    - ModelRequest.instructions → system message prepended before the request's user parts
    - SystemPromptPart → system message
    - UserPromptPart (str) → user message
    - TextPart in ModelResponse → assistant message
    Parts that cannot be represented in plain chat format (tool calls, binary) are skipped.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            # instructions field is the per-request system prompt (anti-injection guard etc.)
            if msg.instructions:
                result.append({"role": "system", "content": msg.instructions})
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    result.append({"role": "system", "content": part.content})
                elif isinstance(part, UserPromptPart):
                    content = part.content if isinstance(part.content, str) else str(part.content)
                    result.append({"role": "user", "content": content})
                # ToolReturnPart and other parts are not needed for summarization
        elif isinstance(msg, ModelResponse):
            text_parts = [p for p in msg.parts if isinstance(p, TextPart)]
            if text_parts:
                combined = " ".join(p.content for p in text_parts)
                result.append({"role": "assistant", "content": combined})
    return result


def build_model(
    model_entry: ModelEntry,
    provider: str,
    llm_host: str,
) -> tuple[OpenAIChatModel | OllamaNativeModel | str, ModelSettings | None]:
    """Construct a pydantic-ai model object and merged ModelSettings for the given provider.

    Per-entry `model_entry.provider` overrides the session-level `provider`.

    Merge precedence (low → high): quirks defaults → quirks extra_body → model_entry.api_params.

    ollama         → OpenAIChatModel wrapping the Ollama OpenAI-compatible API
    ollama-native  → OllamaNativeModel using Ollama's native /api/chat endpoint
    gemini         → bare "google-gla:<model_name>" string (resolved by pydantic-ai)

    Raises ValueError for unsupported providers.
    """
    # Coerce plain string to ModelEntry (supports direct settings mutation in tests)
    if isinstance(model_entry, str):
        model_entry = ModelEntry(model=model_entry)
    model_name = model_entry.model
    normalized = normalize_model_name(model_name)

    # Per-entry provider overrides the session-level provider
    effective_provider = model_entry.provider or provider

    if effective_provider == "ollama-openai":
        inf = get_model_inference("ollama-openai", normalized)
        extra: dict[str, Any] = {}
        num_ctx = inf.get("num_ctx")
        if num_ctx is not None:
            extra["num_ctx"] = num_ctx
        extra.update(inf.get("extra_body", {}))
        # Split api_params: ModelSettings keys (temperature, top_p, max_tokens)
        # override inference defaults; all other keys go into extra_body.
        ms_overrides: dict[str, Any] = {}
        _MS_KEYS = frozenset({"temperature", "top_p", "max_tokens"})
        for k, v in (model_entry.api_params or {}).items():
            if k in _MS_KEYS:
                ms_overrides[k] = v
            else:
                extra[k] = v
        model_settings = ModelSettings(
            temperature=ms_overrides.get("temperature", inf.get("temperature", 0.7)),
            top_p=ms_overrides.get("top_p", inf.get("top_p", 1.0)),
            max_tokens=ms_overrides.get("max_tokens", inf.get("max_tokens", 16384)),
            extra_body=extra,
        )
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=_HTTP_CONNECT_TIMEOUT, read=_HTTP_READ_TIMEOUT, write=_HTTP_WRITE_TIMEOUT, pool=_HTTP_POOL_TIMEOUT)
        )
        model = OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=f"{llm_host}/v1", api_key="ollama", http_client=_http_client),
        )
        return model, model_settings

    if effective_provider == "ollama-native":
        inf = get_model_inference("ollama-native", normalized)
        extra_native: dict[str, Any] = {}
        num_ctx = inf.get("num_ctx")
        if num_ctx is not None:
            extra_native["num_ctx"] = num_ctx
        extra_native.update(inf.get("extra_body", {}))
        ms_overrides_native: dict[str, Any] = {}
        _MS_KEYS_N = frozenset({"temperature", "top_p", "max_tokens"})
        think_override: bool | None = None
        for k, v in (model_entry.api_params or {}).items():
            if k == "think":
                # "think" is a top-level Ollama /api/chat field, not extra_body
                think_override = bool(v)
            elif k in _MS_KEYS_N:
                ms_overrides_native[k] = v
            else:
                extra_native[k] = v
        model_settings_native = ModelSettings(
            temperature=ms_overrides_native.get("temperature", inf.get("temperature", 0.7)),
            top_p=ms_overrides_native.get("top_p", inf.get("top_p", 1.0)),
            max_tokens=ms_overrides_native.get("max_tokens", inf.get("max_tokens", 16384)),
            extra_body=extra_native if extra_native else None,
        )
        model_native = OllamaNativeModel(
            model_name=model_name,
            llm_host=llm_host,
            settings=model_settings_native,
            think=think_override,
        )
        return model_native, model_settings_native

    if effective_provider == "gemini":
        inf = get_model_inference("gemini", normalized)
        if model_entry.api_params:
            logger.warning(
                "Gemini provider does not support extra_body; api_params ignored for model %r: %s",
                model_name, model_entry.api_params,
            )
        model_settings = ModelSettings(
            temperature=inf.get("temperature", 1.0),
            top_p=inf.get("top_p", 0.95),
            max_tokens=inf.get("max_tokens", 65536),
        )
        return f"google-gla:{model_name}", model_settings

    raise ValueError(f"Unsupported provider: {effective_provider!r}")
