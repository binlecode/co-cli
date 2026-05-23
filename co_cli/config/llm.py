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
# Point at the multi-instance Ollama router (snippets-genai/llm_ollama/ollama_router.py),
# which fans requests across two Ollama processes (:11434, :11435) to bypass the
# qwen35moe Parallel=1 lock and yield true concurrent throughput. The router is
# application-protocol transparent — same OpenAI-compatible HTTP API as direct
# Ollama, identical JSON in/out, identical SSE streaming. Set to "http://localhost:11434"
# to bypass the router and talk to the primary Ollama directly.
DEFAULT_LLM_HOST = "http://localhost:11433"

DEFAULT_LLM_MODELS: dict[str, str] = {
    "ollama": "qwen3.6:35b-a3b-agentic",
    "gemini": "gemini-3-flash-preview",
}

DEFAULT_MAX_CTX = 65_536

DEFAULT_MAX_MODEL_REQUESTS_PER_TURN: int = 90


# ---------------------------------------------------------------------------
# Per-model inference settings — canonical knobs per provider/model/mode.
#
# Structure: provider → model (variant-stripped base name) → {reasoning?, noreason?}
# Lookup: self.model.split(":")[0]  (Ollama variants share base entries)
# ---------------------------------------------------------------------------

_LLM_SETTINGS: dict[str, Any] = {
    "ollama": {
        # All Qwen variants (qwen3.6:35b-a3b-agentic, etc.) share this entry via
        # model.split(":")[0]. The -agentic Modelfile is the only Modelfile used —
        # think on/off and all sampling params are overridden at call time.
        "qwen3.6": {
            # Reasoning: agentic turns with thinking enabled.
            # temperature=0.6, top_p=0.95, top_k=20: Modelfile values made explicit
            #   so behavior is deterministic regardless of Modelfile changes.
            # think=True: explicit API override (Modelfile default, but stated clearly).
            # max_tokens=4096 caps each agentic turn; num_ctx must match Modelfile
            #   exactly (65536) to avoid triggering a model reload.
            # extra_body.max_tokens MUST mirror the scalar: pydantic-ai maps the
            #   scalar max_tokens to OpenAI's max_completion_tokens which Ollama
            #   ignores. Only max_tokens at the request root (merged from
            #   extra_body) actually caps Ollama output. Keep both in lockstep.
            "reasoning": {
                "temperature": 0.6,
                "top_p": 0.95,
                "max_tokens": 4096,
                "extra_body": {
                    "think": True,
                    "max_tokens": 4096,
                    "options": {
                        "num_ctx": 65_536,
                        "top_k": 20,
                    },
                },
            },
            # Noreason: used by summarization (context/summarization.py),
            # memory merge (memory/dream.py), and judge calls (evals/_judge.py).
            # think=False + reasoning_effort=none suppresses thinking on the
            # OpenAI-compat path — validated in production via the OpenAI SDK.
            # temperature=0.3: enough entropy for fluent multi-section prose
            #   (summarization can produce 5000+ tokens); 0 causes flat/repetitive
            #   output on long structured summaries. Judge/merge calls are short
            #   enough that 0 vs 0.3 produces no observable difference.
            # top_p=0.8: tighter nucleus than the agentic Modelfile default (0.95).
            # max_tokens=8192: multi-section compaction summaries can exceed 4096;
            #   8192 matches the nothink Modelfile ceiling. Judge/merge calls never
            #   approach this limit.
            # extra_body.max_tokens MUST mirror the scalar: see reasoning entry
            #   above — Ollama only honors max_tokens at the JSON root via
            #   extra_body, not OpenAI's max_completion_tokens.
            # presence_penalty=1.5: loop-breaker for multi-section structured output;
            #   must go in options (not top-level) — _scalar_settings() does not
            #   extract it, so top-level placement would be silently ignored.
            "noreason": {
                "temperature": 0.3,
                "top_p": 0.8,
                "max_tokens": 8192,
                "extra_body": {
                    "think": False,
                    "reasoning_effort": "none",
                    "max_tokens": 8192,
                    "options": {
                        "num_ctx": 65_536,
                        "presence_penalty": 1.5,
                    },
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
    "max_model_requests_per_turn": "CO_LLM_MAX_MODEL_REQUESTS_PER_TURN",
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
    # Optional pinned judge model — used by phase-2 behavioral evals so a regression
    # in the agent doesn't simultaneously regress the judge. Inherits provider/host/
    # api_key from this LlmSettings; only the model name differs. When None, the
    # judge falls back to ``model`` and CaseResult.reason carries [judge_model_same_as_agent].
    judge_model: str | None = Field(default=None)
    # Contract pivot for Ollama context: static num_ctx in _LLM_SETTINGS must be <= max_ctx
    # (ceiling check); probed Modelfile num_ctx must be >= max_ctx (floor check).
    # Both checks use max_ctx as the reference — they do not compare against each other.
    max_ctx: int = Field(default=DEFAULT_MAX_CTX)
    # 0 = disabled (no cap on model requests per turn).
    max_model_requests_per_turn: int = Field(default=DEFAULT_MAX_MODEL_REQUESTS_PER_TURN, ge=0)

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

    def ollama_num_ctx(self) -> int | None:
        """Return the static num_ctx baked into per-call extra_body for this model, or None."""
        if not self.uses_ollama():
            return None
        inference = self._inference("noreason")
        return inference.get("extra_body", {}).get("options", {}).get("num_ctx")

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
