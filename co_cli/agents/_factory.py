"""Provider-aware model factory for main agent and sub-agents."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from co_cli.config import ModelEntry
from co_cli.prompts.model_quirks import get_model_inference, normalize_model_name

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class ResolvedModel:
    """A pre-built model object paired with its inference settings."""

    model: Any
    settings: ModelSettings | None


class ModelRegistry:
    """Session-scoped registry of pre-built ResolvedModel objects keyed by role.

    Built once from config at session start, stored in CoServices, and provides
    pre-built ResolvedModel objects to any in-session component by role lookup.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ResolvedModel] = {}

    @classmethod
    def from_config(cls, config: Any) -> "ModelRegistry":
        """Build the registry from a CoConfig-compatible object.

        Accepts config as untyped to avoid importing CoConfig (no circular import
        risk but keeps _factory.py dep-free of deps.py).
        """
        registry = cls()
        for role, pref_list in config.role_models.items():
            if not pref_list:
                continue
            model, settings = build_model(
                pref_list[0], config.llm_provider, config.ollama_host
            )
            registry._entries[role] = ResolvedModel(model=model, settings=settings)
        return registry

    def get(self, role: str, fallback: ResolvedModel) -> ResolvedModel:
        """Return the ResolvedModel for role, or fallback if unconfigured."""
        return self._entries.get(role, fallback)

    def is_configured(self, role: str) -> bool:
        """Return True if role has a registered ResolvedModel."""
        return role in self._entries


def build_model(
    model_entry: ModelEntry,
    provider: str,
    ollama_host: str,
) -> tuple[OpenAIChatModel | str, ModelSettings | None]:
    """Construct a pydantic-ai model object and merged ModelSettings for the given provider.

    Merge precedence (low → high): quirks defaults → quirks extra_body → model_entry.api_params.

    ollama  → OpenAIChatModel wrapping the Ollama OpenAI-compatible API
    gemini  → bare "google-gla:<model_name>" string (resolved by pydantic-ai)

    Raises ValueError for unsupported providers.
    """
    # Coerce plain string to ModelEntry (supports direct settings mutation in tests)
    if isinstance(model_entry, str):
        model_entry = ModelEntry(model=model_entry)
    model_name = model_entry.model
    normalized = normalize_model_name(model_name)

    if provider == "ollama":
        inf = get_model_inference("ollama", normalized)
        extra: dict[str, Any] = {}
        num_ctx = inf.get("num_ctx")
        if num_ctx is not None:
            extra["num_ctx"] = num_ctx
        extra.update(inf.get("extra_body", {}))
        if model_entry.api_params:
            extra.update(model_entry.api_params)
        model_settings = ModelSettings(
            temperature=inf.get("temperature", 0.7),
            top_p=inf.get("top_p", 1.0),
            max_tokens=inf.get("max_tokens", 16384),
            extra_body=extra,
        )
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
        )
        model = OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=f"{ollama_host}/v1", api_key="ollama", http_client=_http_client),
        )
        return model, model_settings

    if provider == "gemini":
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

    raise ValueError(f"Unsupported provider: {provider!r}")
