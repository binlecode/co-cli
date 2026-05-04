"""LLM provider, model, and inference settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.settings import ModelSettings

# ---------------------------------------------------------------------------
# LLM defaults  (used as LlmSettings field defaults — must stay named)
# ---------------------------------------------------------------------------

DEFAULT_LLM_PROVIDER = "ollama"
DEFAULT_LLM_HOST = "http://localhost:11434"
DEFAULT_LLM_MODEL = "qwen3.6:27b-agentic"
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"


# ---------------------------------------------------------------------------
# Inference defaults tree
#
# Structure: provider → model → {reasoning?, noreason?}
# "_default" is the fallback for any unlisted model.
# Each entry is complete for the modes it defines — no merging between
# _default and model entries; one or the other is used as the base.
# Resolution: model entry (or _default fallback) → merge user explicit config.
# ---------------------------------------------------------------------------

_INFERENCE_DEFAULTS: dict[str, Any] = {
    "ollama": {
        "qwen3.6": {
            "reasoning": {
                "max_tokens": 32768,
            },
            # think=false is the authoritative noreason control; reasoning_effort="none" is a
            # secondary hint honored by Ollama versions that map it to think=false.
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
            # think=false is the authoritative noreason control; reasoning_effort="none" is a
            # secondary hint honored by Ollama versions that map it to think=false.
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
    "num_ctx": "CO_LLM_NUM_CTX",
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
# Settings override models
# ---------------------------------------------------------------------------


class InferenceSettings(BaseModel):
    """User-configurable overrides applied on top of per-model inference defaults.

    Only provider-agnostic scalar params are exposed — provider-specific fields
    (extra_body, thinking_config, num_ctx, etc.) belong in _INFERENCE_DEFAULTS.
    """

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None)
    top_p: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None)


# ---------------------------------------------------------------------------
# LlmSettings
# ---------------------------------------------------------------------------


class LlmSettings(BaseModel):
    """LLM provider, model, and inference settings."""

    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None)
    provider: Literal["ollama", "gemini"] = Field(default=DEFAULT_LLM_PROVIDER)
    host: str = Field(default=DEFAULT_LLM_HOST)
    model: str = Field(default=DEFAULT_LLM_MODEL)
    # Probe result from Ollama bootstrap (Ollama's num_ctx Modelfile parameter). 0 = probe not yet run.
    # Bootstrap writes this; user can also set it in settings.json as a manual override.
    num_ctx: int = Field(default=0)
    # Safety ceiling applied to the probe result. Guards against Ollama reporting an
    # unreasonably large value for quantized models.
    max_ctx: int = Field(default=131072)
    ctx_token_budget: int = Field(default=100_000)
    reasoning: InferenceSettings = Field(default_factory=InferenceSettings)
    noreason: InferenceSettings = Field(default_factory=InferenceSettings)

    def uses_ollama(self) -> bool:
        """Return True when the session LLM backend is Ollama's OpenAI-compatible API."""
        return self.provider == "ollama"

    def uses_gemini(self) -> bool:
        """Return True when the session LLM backend is Gemini."""
        return self.provider == "gemini"

    def effective_num_ctx(self) -> int:
        """Return the effective Ollama context window size.

        Returns 0 when the bootstrap probe has not run (num_ctx unset).
        Otherwise returns the probe result capped by max_ctx.
        """
        raw = self.num_ctx
        if raw <= 0:
            return 0
        return min(raw, self.max_ctx)

    def supports_context_ratio_tracking(self) -> bool:
        """Return True when input/output usage can be compared against an Ollama context budget."""
        return self.uses_ollama() and self.effective_num_ctx() > 0

    def _inference(self, mode: str) -> dict[str, Any]:
        model_key = self.model.split(":")[0]
        base = _INFERENCE_DEFAULTS.get(self.provider, {}).get(model_key, {}).get(mode, {})
        override = (self.reasoning if mode == "reasoning" else self.noreason).model_dump(
            exclude_defaults=True, exclude_none=True
        )
        return {**base, **override}

    def reasoning_model_settings(self) -> ModelSettings:
        """Return ModelSettings for the main reasoning model."""
        inference = self._inference("reasoning")
        extra_body = dict(inference.get("extra_body", {}))
        if (num_ctx := inference.get("num_ctx")) is not None and "num_ctx" not in extra_body:
            extra_body["num_ctx"] = num_ctx
        settings: ModelSettings = {}
        for key in ("temperature", "top_p", "max_tokens"):
            if key in inference:
                settings[key] = inference[key]  # type: ignore[literal-required]
        if extra_body:
            settings["extra_body"] = extra_body
        return settings

    def noreason_model_settings(self) -> ModelSettings:
        """Return ModelSettings for non-reasoning helper calls (provider-aware)."""
        inference = self._inference("noreason")
        if self.uses_gemini():
            from pydantic_ai.models.google import GoogleModelSettings

            kwargs: dict[str, Any] = {
                k: inference[k] for k in ("temperature", "top_p", "max_tokens") if k in inference
            }
            if "thinking_config" in inference:
                kwargs["google_thinking_config"] = dict(inference["thinking_config"])
            return GoogleModelSettings(**kwargs)
        settings: ModelSettings = {}
        for key in ("temperature", "top_p", "max_tokens"):
            if key in inference:
                settings[key] = inference[key]  # type: ignore[literal-required]
        if extra_body := dict(inference.get("extra_body", {})):
            settings["extra_body"] = extra_body
        return settings

    def validate_config(self) -> str | None:
        """Validate LLM config shape — no IO. Returns error message or None if valid."""
        if not self.model:
            return "No model configured — set llm.model in settings.json"
        if self.uses_gemini() and not self.api_key:
            return "Set GEMINI_API_KEY or CO_LLM_API_KEY — required for Gemini provider"
        model_key = self.model.split(":")[0]
        known = _INFERENCE_DEFAULTS.get(self.provider, {})
        if model_key not in known:
            return f"Model {model_key!r} has no inference defaults for provider {self.provider!r}. Known: {', '.join(known)}"
        if "reasoning" not in known[model_key]:
            return f"Model {model_key!r} is noreason-only and cannot be used as the main model"
        return None
