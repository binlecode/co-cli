"""LLM provider, model, and context window settings."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# LLM defaults
# ---------------------------------------------------------------------------

DEFAULT_LLM_PROVIDER = "ollama-openai"
DEFAULT_LLM_HOST = "http://localhost:11434"
DEFAULT_LLM_MODEL = "qwen3.5:35b-a3b-think"
DEFAULT_OLLAMA_NUM_CTX = 262144
DEFAULT_CTX_WARN_THRESHOLD = 0.85
DEFAULT_CTX_OVERFLOW_THRESHOLD = 1.0


# ---------------------------------------------------------------------------
# LlmSettings
# ---------------------------------------------------------------------------


class LlmSettings(BaseModel):
    """LLM provider, model, and context window settings."""
    model_config = ConfigDict(extra="ignore")

    api_key: Optional[str] = Field(default=None)
    provider: Literal["ollama-openai", "gemini"] = Field(default=DEFAULT_LLM_PROVIDER)
    host: str = Field(default=DEFAULT_LLM_HOST)
    model: str = Field(default=DEFAULT_LLM_MODEL)
    # IMPORTANT: Use -agentic Modelfile variants for models that need custom num_ctx.
    # Ollama's OpenAI-compatible API ignores num_ctx from request params — it MUST
    # be baked into the Modelfile via PARAMETER num_ctx.
    num_ctx: int = Field(default=DEFAULT_OLLAMA_NUM_CTX)
    ctx_warn_threshold: float = Field(default=DEFAULT_CTX_WARN_THRESHOLD)
    ctx_overflow_threshold: float = Field(default=DEFAULT_CTX_OVERFLOW_THRESHOLD)

    def uses_ollama_openai(self) -> bool:
        """Return True when the session LLM backend is Ollama's OpenAI-compatible API."""
        return self.provider == "ollama-openai"

    def uses_gemini(self) -> bool:
        """Return True when the session LLM backend is Gemini."""
        return self.provider == "gemini"

    def supports_context_ratio_tracking(self) -> bool:
        """Return True when input/output usage can be compared against an Ollama context budget."""
        return self.uses_ollama_openai() and self.num_ctx > 0

    def validate_config(self) -> str | None:
        """Validate LLM config shape — no IO. Returns error message or None if valid."""
        if not self.model:
            return "No model configured — set llm.model in settings.json"
        if self.uses_gemini() and not self.api_key:
            return "LLM_API_KEY not set — required for Gemini provider"
        return None
