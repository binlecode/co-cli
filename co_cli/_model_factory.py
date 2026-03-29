"""Provider-aware model factory — builds and caches LLM model objects by role."""

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from co_cli.config import ModelConfig
from co_cli.prompts.model_quirks._loader import get_model_inference, normalize_model_name

logger = logging.getLogger(__name__)

_HTTP_CONNECT_TIMEOUT = 10.0
_HTTP_READ_TIMEOUT = 300.0
_HTTP_WRITE_TIMEOUT = 30.0
_HTTP_POOL_TIMEOUT = 10.0


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
        for role, entry in config.role_models.items():
            if not entry:
                continue
            model, settings = build_model(entry, config.llm_provider, config.llm_host, api_key=config.llm_api_key)
            registry._models[role] = ResolvedModel(model=model, settings=settings)
        return registry

    def get(self, role: str, fallback: ResolvedModel) -> ResolvedModel:
        """Return the ResolvedModel for role, or fallback if unconfigured."""
        return self._models.get(role, fallback)

    def is_configured(self, role: str) -> bool:
        """Return True if role has a registered ResolvedModel."""
        return role in self._models


def build_model(
    model_entry: ModelConfig,
    provider: str,
    llm_host: str,
    api_key: str | None = None,
) -> tuple[OpenAIChatModel | GoogleModel, ModelSettings | None]:
    """Construct a pydantic-ai model object and merged ModelSettings for the given provider.

    Per-entry `model_entry.provider` overrides the session-level `provider`.

    Merge precedence (low → high): quirks defaults → quirks extra_body → model_entry.api_params.

    ollama-openai  → OpenAIChatModel wrapping the Ollama OpenAI-compatible API
    gemini         → GoogleModel with GoogleProvider (api_key injected via constructor)

    Raises ValueError for unsupported providers.
    """
    # Coerce plain string to ModelConfig (supports direct settings mutation in tests)
    if isinstance(model_entry, str):
        model_entry = ModelConfig(model=model_entry)
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
        google_model = GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
        return google_model, model_settings

    raise ValueError(f"Unsupported provider: {effective_provider!r}")
