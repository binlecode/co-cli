"""LLM provider, model, and inference settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai.settings import ModelSettings

# ---------------------------------------------------------------------------
# LLM defaults
# ---------------------------------------------------------------------------

DEFAULT_LLM_PROVIDER = "ollama"
DEFAULT_LLM_HOST = "http://localhost:11434"

DEFAULT_LLM_MODELS: dict[str, str] = {
    "ollama": "qwen3.6:27b-agentic",
    "gemini": "gemini-3-flash-preview",
}

DEFAULT_MAX_CTX = 131_072
DEFAULT_CTX_TOKEN_BUDGET = 100_000


# ---------------------------------------------------------------------------
# Per-model inference settings — canonical knobs per provider/model/mode.
#
# Structure: provider → model (variant-stripped base name) → {reasoning?, noreason?}
# Lookup: self.model.split(":")[0]  (Ollama variants share base entries)
# ---------------------------------------------------------------------------

_LLM_SETTINGS: dict[str, Any] = {
    "ollama": {
        "qwen3.6": {
            "reasoning": {
                "max_tokens": 32768,
            },
            # reasoning_effort="none" is the canonical noreason knob on the OpenAI-compatible
            # path; think=false is belt-and-suspenders for older Ollama versions.
            "noreason": {
                "extra_body": {
                    "think": False,
                    "reasoning_effort": "none",
                },
            },
        },
        "qwen3.5": {
            "reasoning": {
                "max_tokens": 32768,
            },
            "noreason": {
                "extra_body": {
                    "think": False,
                    "reasoning_effort": "none",
                },
            },
        },
    },
    "gemini": {
        "gemini-3-flash-preview": {
            "reasoning": {
                "temperature": 1.0,
                "top_p": 0.95,
                "max_tokens": 65536,
            },
            # MINIMAL is the lowest ThinkingLevel for Gemini 3 models; keeps helper calls fast.
            "noreason": {
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 16384,
                "thinking_config": {"thinking_level": "MINIMAL"},
            },
        },
        # Gemini 2.5 Flash/Flash-Lite: noreason-only — thinking_budget=0 disables thinking.
        # Cannot be used as the main reasoning model (no reasoning entry).
        "gemini-2.5-flash": {
            "noreason": {
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 16384,
                "thinking_config": {"thinking_budget": 0},
            },
        },
        "gemini-2.5-flash-lite": {
            "noreason": {
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 16384,
                "thinking_config": {"thinking_budget": 0},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

LLM_ENV_MAP: dict[str, str] = {
    "provider": "CO_LLM_PROVIDER",
    "host": "CO_LLM_HOST",
    "model": "CO_LLM_MODEL",
}

_PROVIDER_API_KEY_VARS: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
}


def resolve_api_key_from_env(env: Mapping[str, str], llm_data: dict) -> str | None:
    """Resolve LLM API key from env: provider-specific var wins, CO_LLM_API_KEY fallback."""
    provider = (
        env.get("CO_LLM_PROVIDER")
        or (llm_data.get("provider") if isinstance(llm_data, dict) else None)
        or DEFAULT_LLM_PROVIDER
    )
    specific_var = _PROVIDER_API_KEY_VARS.get(provider)
    return (specific_var and env.get(specific_var)) or env.get("CO_LLM_API_KEY") or None


# ---------------------------------------------------------------------------
# Inference dict → pydantic-ai ModelSettings translators
# ---------------------------------------------------------------------------


def _scalar_settings(inference: dict[str, Any]) -> dict[str, Any]:
    return {k: inference[k] for k in ("temperature", "top_p", "max_tokens") if k in inference}


def _ollama_settings(inference: dict[str, Any]) -> ModelSettings:
    settings: ModelSettings = _scalar_settings(inference)  # type: ignore[assignment]
    if extra_body := dict(inference.get("extra_body", {})):
        settings["extra_body"] = extra_body
    return settings


def _gemini_settings(inference: dict[str, Any]) -> ModelSettings:
    from pydantic_ai.models.google import GoogleModelSettings

    kwargs = _scalar_settings(inference)
    if thinking_config := inference.get("thinking_config"):
        kwargs["google_thinking_config"] = dict(thinking_config)
    return GoogleModelSettings(**kwargs)


# ---------------------------------------------------------------------------
# LlmSettings
# ---------------------------------------------------------------------------


class LlmSettings(BaseModel):
    """LLM provider, model, and inference settings."""

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None)
    provider: Literal["ollama", "gemini"] = Field(default=DEFAULT_LLM_PROVIDER)
    host: str = Field(default=DEFAULT_LLM_HOST)
    model: str = Field(default="")
    # User-configurable ceiling; probed Ollama num_ctx is capped to this at bootstrap.
    max_ctx: int = Field(default=DEFAULT_MAX_CTX)
    ctx_token_budget: int = Field(default=DEFAULT_CTX_TOKEN_BUDGET)

    @model_validator(mode="after")
    def _default_model_per_provider(self) -> LlmSettings:
        if not self.model:
            self.model = DEFAULT_LLM_MODELS[self.provider]
        return self

    def uses_ollama(self) -> bool:
        """Return True when the session LLM backend is Ollama's OpenAI-compatible API."""
        return self.provider == "ollama"

    def uses_gemini(self) -> bool:
        """Return True when the session LLM backend is Gemini."""
        return self.provider == "gemini"

    def _inference(self, mode: str) -> dict[str, Any]:
        model_key = self.model.split(":")[0]
        return _LLM_SETTINGS.get(self.provider, {}).get(model_key, {}).get(mode, {})

    def reasoning_model_settings(self) -> ModelSettings:
        """Return ModelSettings for the main reasoning model (provider-aware)."""
        inference = self._inference("reasoning")
        return _gemini_settings(inference) if self.uses_gemini() else _ollama_settings(inference)

    def noreason_model_settings(self) -> ModelSettings:
        """Return ModelSettings for non-reasoning helper calls (provider-aware)."""
        inference = self._inference("noreason")
        return _gemini_settings(inference) if self.uses_gemini() else _ollama_settings(inference)

    def validate_config(self) -> str | None:
        """Validate LLM config shape — no IO. Returns error message or None if valid."""
        if self.uses_gemini() and not self.api_key:
            return "Set GEMINI_API_KEY or CO_LLM_API_KEY — required for Gemini provider"
        model_key = self.model.split(":")[0]
        known = _LLM_SETTINGS.get(self.provider, {})
        if model_key not in known:
            return f"Model {model_key!r} has no inference defaults for provider {self.provider!r}. Known: {', '.join(known)}"
        if "reasoning" not in known[model_key]:
            return f"Model {model_key!r} is noreason-only and cannot be used as the main model"
        return None
