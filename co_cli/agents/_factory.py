"""Provider-aware sub-agent model factory."""

from typing import TYPE_CHECKING, Any

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from co_cli.config import ModelEntry

if TYPE_CHECKING:
    from co_cli.deps import CoConfig


def make_subagent_model(
    model_entry: ModelEntry,
    provider: str,
    ollama_host: str,
) -> OpenAIChatModel | str:
    """Construct a pydantic-ai model object for the given provider.

    ollama  → OpenAIChatModel wrapping the Ollama OpenAI-compatible API
    gemini  → bare "google-gla:<model_name>" string (resolved by pydantic-ai)

    Raises ValueError for unsupported providers.
    """
    model_name = model_entry.model
    if provider == "ollama":
        model_settings = {"extra_body": model_entry.api_params} if model_entry.api_params else None
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=f"{ollama_host}/v1", api_key="ollama"),
            settings=model_settings,
        )
    if provider == "gemini":
        return f"google-gla:{model_name}"
    raise ValueError(f"Unsupported provider: {provider!r}")


def resolve_role_model(config: "CoConfig", role: str, fallback: Any) -> Any:
    """Return the head model for *role* from config, or *fallback* if the role is unconfigured.

    Centralises the repeated pattern:
        pref_list = config.role_models.get(role, [])
        entry = pref_list[0] if pref_list else None
        model = make_subagent_model(entry, ...) if entry else fallback
    """
    model_pref_list = config.role_models.get(role, [])
    model_entry = model_pref_list[0] if model_pref_list else None
    if model_entry:
        return make_subagent_model(model_entry, config.llm_provider, config.ollama_host)
    return fallback
