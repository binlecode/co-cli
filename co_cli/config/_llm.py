"""LLM provider, model roles, and context window settings."""
from copy import deepcopy
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_NOREASON_MODEL: dict[str, Any] = {
    "model": "qwen3.5:35b-a3b-think",
    "provider": "ollama-openai",
    "api_params": {
        "temperature": 0.7,
        "top_p": 0.8,
        "max_tokens": 16384,
        "reasoning_effort": "none",
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
        "num_ctx": 131072,
        "num_predict": 16384,
    },
}

DEFAULT_OLLAMA_REASONING_MODEL: dict[str, Any] = {
    "model": "qwen3.5:35b-a3b-think",
    "provider": "ollama-openai",
    "api_params": {
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 32768,
        "num_ctx": 131072,
        "num_predict": 32768,
    },
}
# Summarization reuses the think model over the OpenAI-compatible Ollama API.
# Request-level reasoning_effort="none" suppresses reasoning output while keeping
# the same weights resident, avoiding an instruct-model swap.
DEFAULT_OLLAMA_SUMMARIZATION_MODEL = deepcopy(DEFAULT_OLLAMA_NOREASON_MODEL)
DEFAULT_OLLAMA_ANALYSIS_MODEL = deepcopy(DEFAULT_OLLAMA_NOREASON_MODEL)
DEFAULT_OLLAMA_CODING_MODEL: dict[str, Any] = {
    "model": "qwen3.5:35b-a3b-code",
    "provider": "ollama-openai",
}
DEFAULT_OLLAMA_RESEARCH_MODEL = deepcopy(DEFAULT_OLLAMA_NOREASON_MODEL)
DEFAULT_GEMINI_REASONING_MODEL: dict[str, Any] = {
    "model": "gemini-3-flash-preview",
    "provider": "gemini",
}

# ---------------------------------------------------------------------------
# LLM defaults
# ---------------------------------------------------------------------------

DEFAULT_LLM_PROVIDER = "ollama-openai"
DEFAULT_LLM_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_NUM_CTX = 262144
DEFAULT_CTX_WARN_THRESHOLD = 0.85
DEFAULT_CTX_OVERFLOW_THRESHOLD = 1.0

# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

ROLE_REASONING = "reasoning"
ROLE_SUMMARIZATION = "summarization"
ROLE_CODING = "coding"
ROLE_RESEARCH = "research"
ROLE_ANALYSIS = "analysis"
VALID_ROLE_NAMES: frozenset[str] = frozenset({
    ROLE_REASONING, ROLE_SUMMARIZATION, ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS,
})

# ---------------------------------------------------------------------------
# Shared model types
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    """A single model entry in a role chain, with optional API parameters."""

    model: str
    api_params: dict[str, Any] = Field(default_factory=dict)
    provider: Literal["ollama-openai", "gemini"]


# ---------------------------------------------------------------------------
# LlmSettings
# ---------------------------------------------------------------------------


class LlmSettings(BaseModel):
    """LLM provider, model roles, and context window settings."""
    model_config = ConfigDict(extra="ignore")

    api_key: Optional[str] = Field(default=None)
    provider: str = Field(default=DEFAULT_LLM_PROVIDER)
    host: str = Field(default=DEFAULT_LLM_HOST)
    # IMPORTANT: Use -agentic Modelfile variants for models that need custom num_ctx.
    # Ollama's OpenAI-compatible API ignores num_ctx from request params — it MUST
    # be baked into the Modelfile via PARAMETER num_ctx.
    num_ctx: int = Field(default=DEFAULT_OLLAMA_NUM_CTX)
    ctx_warn_threshold: float = Field(default=DEFAULT_CTX_WARN_THRESHOLD)
    ctx_overflow_threshold: float = Field(default=DEFAULT_CTX_OVERFLOW_THRESHOLD)
    role_models: dict[str, ModelConfig] = Field(default_factory=dict)

    @field_validator("role_models", mode="before")
    @classmethod
    def _parse_role_models(cls, v: dict[str, Any] | None) -> dict[str, dict]:
        if not v:
            return {}
        parsed: dict[str, dict] = {}
        for role, model in v.items():
            if isinstance(model, str):
                raise ValueError(
                    f"llm.role_models.{role} must be an object with explicit 'model' and 'provider' keys"
                )
            elif isinstance(model, dict):
                parsed[str(role)] = model
            else:
                parsed[str(role)] = model.model_dump() if hasattr(model, "model_dump") else {"model": str(model)}
        return parsed

    @model_validator(mode="after")
    def _validate_model_role_keys(self) -> "LlmSettings":
        unknown = set(self.role_models.keys()) - VALID_ROLE_NAMES
        if unknown:
            raise ValueError(
                f"Unknown llm.role_models keys: {sorted(unknown)}. "
                f"Valid roles: {sorted(VALID_ROLE_NAMES)}"
            )
        return self

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
        if not self.role_models.get("reasoning"):
            return "No reasoning model configured — set llm.role_models.reasoning in settings.json"
        if self.uses_gemini() and not self.api_key:
            return "LLM_API_KEY not set — required for Gemini provider"
        return None
