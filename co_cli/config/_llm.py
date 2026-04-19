"""LLM provider, model, and inference settings."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.settings import ModelSettings

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
# Inference settings
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_TEMPERATURE = 0.7
DEFAULT_OLLAMA_TOP_P = 1.0
DEFAULT_OLLAMA_MAX_TOKENS = 16384

DEFAULT_GEMINI_TEMPERATURE = 1.0
DEFAULT_GEMINI_TOP_P = 0.95
DEFAULT_GEMINI_MAX_TOKENS = 65536

DEFAULT_NOREASON_TEMPERATURE = 0.7
DEFAULT_NOREASON_TOP_P = 0.8
DEFAULT_NOREASON_MAX_TOKENS = 16384

DEFAULT_NOREASON_EXTRA_BODY: dict[str, Any] = {
    "reasoning_effort": "none",
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repeat_penalty": 1.0,
    "num_ctx": 131072,
    "num_predict": 16384,
}


class ModelInference(TypedDict, total=False):
    """Resolved model inference parameters used at runtime."""

    temperature: float
    top_p: float
    max_tokens: int
    num_ctx: int
    context_window: int
    extra_body: dict[str, Any]
    thinking_config: dict[str, Any]


class ReasoningSettings(BaseModel):
    """Optional explicit overrides for the main reasoning model."""

    model_config = ConfigDict(extra="ignore")

    temperature: float | None = Field(default=None)
    top_p: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None)
    num_ctx: int | None = Field(default=None)
    context_window: int | None = Field(default=None)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class NoReasonSettings(BaseModel):
    """Optional explicit overrides for non-reasoning helper calls."""

    model_config = ConfigDict(extra="ignore")

    temperature: float | None = Field(default=None)
    top_p: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None)
    extra_body: dict[str, Any] = Field(default_factory=dict)


_PROVIDER_REASONING_DEFAULTS: dict[str, ModelInference] = {
    "ollama-openai": {
        "temperature": DEFAULT_OLLAMA_TEMPERATURE,
        "top_p": DEFAULT_OLLAMA_TOP_P,
        "max_tokens": DEFAULT_OLLAMA_MAX_TOKENS,
    },
    "gemini": {
        "temperature": DEFAULT_GEMINI_TEMPERATURE,
        "top_p": DEFAULT_GEMINI_TOP_P,
        "max_tokens": DEFAULT_GEMINI_MAX_TOKENS,
    },
}

_MODEL_REASONING_DEFAULTS: dict[tuple[str, str], ModelInference] = {
    ("gemini", "gemini-3.1-flash-preview"): {
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 65536,
        "context_window": 1048576,
    },
    ("gemini", "gemini-3.1-pro-preview"): {
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 65536,
        "context_window": 1048576,
    },
    ("ollama-openai", "qwen3"): {
        "temperature": 0.6,
        "top_p": 0.95,
        "max_tokens": 32768,
        "num_ctx": 262144,
        "context_window": 262144,
        "extra_body": {
            "top_k": 20,
            "repeat_penalty": 1.0,
        },
    },
    ("ollama-openai", "qwen3.5"): {
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 32768,
        "context_window": 262144,
        "extra_body": {
            "top_k": 20,
            "min_p": 0,
            "presence_penalty": 1.5,
        },
    },
}

_PROVIDER_NOREASON_DEFAULTS: dict[str, ModelInference] = {
    "ollama-openai": {
        "temperature": DEFAULT_NOREASON_TEMPERATURE,
        "top_p": DEFAULT_NOREASON_TOP_P,
        "max_tokens": DEFAULT_NOREASON_MAX_TOKENS,
        "extra_body": dict(DEFAULT_NOREASON_EXTRA_BODY),
    },
    "gemini": {
        "temperature": DEFAULT_NOREASON_TEMPERATURE,
        "top_p": DEFAULT_NOREASON_TOP_P,
        "max_tokens": DEFAULT_NOREASON_MAX_TOKENS,
        # Gemini 3 Flash-class default. Goes into GoogleModelSettings
        # google_thinking_config (not ModelSettings.extra_body — Gemini
        # uses the native path, not the OpenAI-compat channel).
        "thinking_config": {"thinking_level": "minimal"},
    },
}

# Model-specific noreason overrides — Gemini 3.1 Pro does not support
# "minimal"; "low" is the minimum reasoning setting (per Google docs).
# Gemini 2.5 Flash/Flash-Lite use thinking_budget=0 instead of thinking_level.
_MODEL_NOREASON_DEFAULTS: dict[tuple[str, str], ModelInference] = {
    ("gemini", "gemini-3.1-pro-preview"): {
        "thinking_config": {"thinking_level": "low"},
    },
    ("gemini", "gemini-2.5-flash"): {
        "thinking_config": {"thinking_budget": 0},
    },
    ("gemini", "gemini-2.5-flash-lite"): {
        "thinking_config": {"thinking_budget": 0},
    },
}


def normalize_model_name(model_name: str) -> str:
    """Normalize model name for model-default lookup by stripping quantization tags."""
    return model_name.split(":")[0]


def _merge_inference(base: ModelInference, override: ModelInference) -> ModelInference:
    """Merge two model inference dicts, combining extra_body shallowly."""
    merged: ModelInference = dict(base)
    base_extra = dict(base.get("extra_body", {}))
    override_extra = dict(override.get("extra_body", {}))
    for key, value in override.items():
        if key == "extra_body":
            continue
        merged[key] = value
    combined_extra = base_extra
    combined_extra.update(override_extra)
    if combined_extra:
        merged["extra_body"] = combined_extra
    elif "extra_body" in merged:
        del merged["extra_body"]
    return merged


def resolve_reasoning_inference(llm: LlmSettings) -> ModelInference:
    """Resolve the effective reasoning-model inference settings."""
    normalized_model = normalize_model_name(llm.model)
    provider_defaults = _PROVIDER_REASONING_DEFAULTS.get(llm.provider, {})
    model_defaults = _MODEL_REASONING_DEFAULTS.get((llm.provider, normalized_model), {})
    resolved = _merge_inference(provider_defaults, model_defaults)
    explicit = llm.reasoning.model_dump(exclude_defaults=True, exclude_none=True)
    return _merge_inference(resolved, explicit)


def resolve_noreason_inference(llm: LlmSettings) -> ModelInference:
    """Resolve the effective noreason inference settings — symmetric with resolve_reasoning_inference."""
    normalized_model = normalize_model_name(llm.model)
    provider_defaults = _PROVIDER_NOREASON_DEFAULTS.get(llm.provider, {})
    model_defaults = _MODEL_NOREASON_DEFAULTS.get((llm.provider, normalized_model), {})
    resolved = _merge_inference(provider_defaults, model_defaults)
    explicit = llm.noreason.model_dump(exclude_defaults=True, exclude_none=True)
    return _merge_inference(resolved, explicit)


# ---------------------------------------------------------------------------
# LlmSettings
# ---------------------------------------------------------------------------


class LlmSettings(BaseModel):
    """LLM provider, model, and inference settings."""

    model_config = ConfigDict(extra="ignore")

    api_key: str | None = Field(default=None)
    provider: Literal["ollama-openai", "gemini"] = Field(default=DEFAULT_LLM_PROVIDER)
    host: str = Field(default=DEFAULT_LLM_HOST)
    model: str = Field(default=DEFAULT_LLM_MODEL)
    # IMPORTANT: Use -agentic Modelfile variants for models that need custom num_ctx.
    # Ollama's OpenAI-compatible API ignores num_ctx from request params — it MUST
    # be baked into the Modelfile via PARAMETER num_ctx.
    num_ctx: int = Field(default=DEFAULT_OLLAMA_NUM_CTX)
    ctx_warn_threshold: float = Field(default=DEFAULT_CTX_WARN_THRESHOLD)
    ctx_overflow_threshold: float = Field(default=DEFAULT_CTX_OVERFLOW_THRESHOLD)
    reasoning: ReasoningSettings = Field(default_factory=ReasoningSettings)
    noreason: NoReasonSettings = Field(default_factory=NoReasonSettings)

    def uses_ollama_openai(self) -> bool:
        """Return True when the session LLM backend is Ollama's OpenAI-compatible API."""
        return self.provider == "ollama-openai"

    def uses_gemini(self) -> bool:
        """Return True when the session LLM backend is Gemini."""
        return self.provider == "gemini"

    def supports_context_ratio_tracking(self) -> bool:
        """Return True when input/output usage can be compared against an Ollama context budget."""
        return self.uses_ollama_openai() and self.num_ctx > 0

    def reasoning_model_settings(self) -> ModelSettings:
        """Return ModelSettings for the main reasoning model."""
        inference = resolve_reasoning_inference(self)
        extra_body = dict(inference.get("extra_body", {}))
        num_ctx = inference.get("num_ctx")
        if num_ctx is not None and "num_ctx" not in extra_body:
            extra_body["num_ctx"] = num_ctx
        return ModelSettings(
            temperature=inference["temperature"],
            top_p=inference["top_p"],
            max_tokens=inference["max_tokens"],
            extra_body=extra_body,
        )

    def reasoning_context_window(self) -> int | None:
        """Return the configured or model-default context window for the main model."""
        return resolve_reasoning_inference(self).get("context_window")

    def noreason_model_settings(self) -> ModelSettings:
        """Return ModelSettings for non-reasoning helper calls (provider-aware)."""
        inference = resolve_noreason_inference(self)
        if self.uses_gemini():
            from pydantic_ai.models.google import GoogleModelSettings

            kwargs: dict[str, Any] = {
                "temperature": inference["temperature"],
                "top_p": inference["top_p"],
                "max_tokens": inference["max_tokens"],
            }
            if "thinking_config" in inference:
                kwargs["google_thinking_config"] = dict(inference["thinking_config"])
            return GoogleModelSettings(**kwargs)
        return ModelSettings(
            temperature=inference["temperature"],
            top_p=inference["top_p"],
            max_tokens=inference["max_tokens"],
            extra_body=dict(inference.get("extra_body", {})),
        )

    def validate_config(self) -> str | None:
        """Validate LLM config shape — no IO. Returns error message or None if valid."""
        if not self.model:
            return "No model configured — set llm.model in settings.json"
        if self.uses_gemini() and not self.api_key:
            return "Set GEMINI_API_KEY or LLM_API_KEY — required for Gemini provider"
        return None
