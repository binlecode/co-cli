"""Provider-aware sub-agent model factory."""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


def make_subagent_model(
    model_name: str,
    provider: str,
    ollama_host: str,
) -> OpenAIChatModel | str:
    """Construct a pydantic-ai model object for the given provider.

    ollama  → OpenAIChatModel wrapping the Ollama OpenAI-compatible API
    gemini  → bare "google-gla:<model_name>" string (resolved by pydantic-ai)

    Raises ValueError for unsupported providers.
    """
    if provider == "ollama":
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=f"{ollama_host}/v1", api_key="ollama"),
        )
    if provider == "gemini":
        return f"google-gla:{model_name}"
    raise ValueError(f"Unsupported provider: {provider!r}")
