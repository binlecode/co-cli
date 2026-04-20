"""LLM provider, model, and inference settings."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.settings import ModelSettings

# ---------------------------------------------------------------------------
# LLM defaults  (used as LlmSettings field defaults — must stay named)
# ---------------------------------------------------------------------------

DEFAULT_LLM_PROVIDER = "ollama"
DEFAULT_LLM_HOST = "http://localhost:11434"
DEFAULT_LLM_MODEL = "qwen3.5:35b-a3b-think"


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
        # Settings sourced from ollama/Modelfile.qwen3.5-35b-a3b-think
        "qwen3.5": {
            "reasoning": {
                "temperature": 1.0,
                "top_p": 0.95,
                "max_tokens": 32768,
                "context_window": 131072,
                "extra_body": {
                    "top_k": 20,
                    "min_p": 0.0,
                    "presence_penalty": 1.5,
                    "repeat_penalty": 1.0,
                },
            },
            # think=false is the authoritative noreason control for qwen3.5 via Ollama.
            # reasoning_effort="none" is a secondary hint — honored by Ollama versions that
            # map it to think=false, silently ignored by versions that do not.
            "noreason": {
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 16384,
                "extra_body": {
                    "think": False,
                    "reasoning_effort": "none",
                    "top_k": 20,
                    "min_p": 0.0,
                    "presence_penalty": 1.5,
                    "repeat_penalty": 1.0,
                    "num_ctx": 131072,
                    "num_predict": 16384,
                },
            },
        },
    },
    "gemini": {
        "gemini-3.1-flash-preview": {
            "reasoning": {
                "temperature": 1.0,
                "top_p": 0.95,
                "max_tokens": 65536,
                "context_window": 1048576,
            },
            # Flash supports "minimal" thinking level.
            "noreason": {
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 16384,
                "thinking_config": {"thinking_level": "minimal"},
            },
        },
        "gemini-3.1-pro-preview": {
            "reasoning": {
                "temperature": 1.0,
                "top_p": 0.95,
                "max_tokens": 65536,
                "context_window": 1048576,
            },
            # Pro does not support "minimal"; "low" is the minimum (per Google docs).
            "noreason": {
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 16384,
                "thinking_config": {"thinking_level": "low"},
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
# Settings override models
# ---------------------------------------------------------------------------


class InferenceOverride(BaseModel):
    """User-configurable overrides applied on top of per-model inference defaults.

    Only provider-agnostic scalar params are exposed — provider-specific fields
    (extra_body, thinking_config, num_ctx, etc.) belong in _INFERENCE_DEFAULTS.
    """

    model_config = ConfigDict(extra="ignore")

    temperature: float | None = Field(default=None)
    top_p: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None)


# ---------------------------------------------------------------------------
# LlmSettings
# ---------------------------------------------------------------------------


class LlmSettings(BaseModel):
    """LLM provider, model, and inference settings."""

    model_config = ConfigDict(extra="ignore")

    api_key: str | None = Field(default=None)
    provider: Literal["ollama", "gemini"] = Field(default=DEFAULT_LLM_PROVIDER)
    host: str = Field(default=DEFAULT_LLM_HOST)
    model: str = Field(default=DEFAULT_LLM_MODEL)
    # User/bootstrap override for Ollama context window. 0 = unset (use model spec).
    # Bootstrap sets this from the runtime probe; user can also set it in settings.json.
    num_ctx: int = Field(default=0)
    ctx_token_budget: int = Field(default=100_000)
    ctx_output_reserve: int = Field(default=16_384)
    ctx_warn_threshold: float = Field(default=0.85)
    ctx_overflow_threshold: float = Field(default=1.0)
    reasoning: InferenceOverride = Field(default_factory=InferenceOverride)
    noreason: InferenceOverride = Field(default_factory=InferenceOverride)

    def uses_ollama(self) -> bool:
        """Return True when the session LLM backend is Ollama's OpenAI-compatible API."""
        return self.provider == "ollama"

    def uses_gemini(self) -> bool:
        """Return True when the session LLM backend is Gemini."""
        return self.provider == "gemini"

    def effective_num_ctx(self) -> int:
        """Return the effective Ollama context window size.

        Resolution order: explicit num_ctx override (user/bootstrap) → model spec
        context_window → 0 (unknown).
        """
        if self.num_ctx > 0:
            return self.num_ctx
        return self.reasoning_context_window() or 0

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
        return ModelSettings(
            temperature=inference["temperature"],
            top_p=inference["top_p"],
            max_tokens=inference["max_tokens"],
            extra_body=extra_body,
        )

    def reasoning_context_window(self) -> int | None:
        """Return the configured or model-default context window for the main model."""
        return self._inference("reasoning").get("context_window")

    def noreason_model_settings(self) -> ModelSettings:
        """Return ModelSettings for non-reasoning helper calls (provider-aware)."""
        inference = self._inference("noreason")
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
            return "Set GEMINI_API_KEY or CO_LLM_API_KEY — required for Gemini provider"
        model_key = self.model.split(":")[0]
        known = _INFERENCE_DEFAULTS.get(self.provider, {})
        if model_key not in known:
            return f"Model {model_key!r} has no inference defaults for provider {self.provider!r}. Known: {', '.join(known)}"
        if "reasoning" not in known[model_key]:
            return f"Model {model_key!r} is noreason-only and cannot be used as the main model"
        return None
