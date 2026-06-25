"""LLM provider, model, and inference settings."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
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

MAX_CONTEXT_TOKENS = 65_536

# Frontier (cloud reasoner) context budget — half the provider's 1M max window.
# co's compaction_ratio (0.50, hermes-parity single-shot 50%) clamps off this budget;
# the pricing-cliff cost-clamp is deferred to a post-calibration setting (see the
# model-profile-01-seam plan, OQ-3).
FRONTIER_MAX_CONTEXT_TOKENS = 524_288

MAX_MODEL_REQUESTS_PER_TURN: int = 40

RUN_STALL_TIMEOUT_SECS: float = 120.0


class ModelProfile(StrEnum):
    """Binary model class driving context budget (and, later, prompt overlays).

    WEAK_LOCAL: the local MoE the behavioral rules are calibrated to counter.
    FRONTIER: a cloud reasoner with a large native window and different prompt needs.
    """

    WEAK_LOCAL = "weak_local"
    FRONTIER = "frontier"


def resolve_model_profile(llm: LlmSettings) -> ModelProfile:
    """Resolve the model profile from the configured provider.

    Ollama is the weak local backend; every other provider (gemini today) is a
    frontier cloud reasoner. Binary by provider — the only distinction with evidence.
    """
    return ModelProfile.WEAK_LOCAL if llm.uses_ollama() else ModelProfile.FRONTIER


def profile_max_context_tokens(profile: ModelProfile) -> int:
    """Return the default context budget for a profile.

    WEAK_LOCAL is a hard 64k clamp — the baseline the weak-model profiling is
    calibrated against, never relaxed. FRONTIER is half the provider's 1M max window.
    """
    if profile is ModelProfile.FRONTIER:
        return FRONTIER_MAX_CONTEXT_TOKENS
    return MAX_CONTEXT_TOKENS


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
            # max_tokens=8192 caps each agentic turn (matches noreason): thinking and
            #   answer share one pool on Ollama, so 4096 could be fully consumed by
            #   reasoning before any answer token, raising "token limit exceeded before
            #   any response". 8192 gives the answer headroom after thinking. num_ctx
            #   must match Modelfile exactly (65536) to avoid triggering a model reload.
            # extra_body.max_tokens MUST mirror the scalar: pydantic-ai maps the
            #   scalar max_tokens to OpenAI's max_completion_tokens which Ollama
            #   ignores. Only max_tokens at the request root (merged from
            #   extra_body) actually caps Ollama output. Keep both in lockstep.
            "reasoning": {
                "temperature": 0.6,
                "top_p": 0.95,
                "max_tokens": 8192,
                "extra_body": {
                    "think": True,
                    "max_tokens": 8192,
                    "options": {
                        "num_ctx": 65_536,
                        "top_k": 20,
                    },
                },
            },
            # Noreason: used by summarization (context/summarization.py),
            # memory merge (daemons/dream/_housekeeping.py), and judge calls (evals/_judge.py).
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
        # Pro-tier reasoner: full main-agent backend (reasoning + noreason).
        # thinking_level pinned explicitly — HIGH for depth on the main agent, LOW
        # to keep helper calls fast. NOTE: pro rejects MINIMAL (flash-only); LOW is the
        # lowest level pro supports. Output capped at the model's 65,536 ceiling.
        "gemini-3.1-pro-preview": {
            "reasoning": {
                "temperature": 1.0,
                "top_p": 0.95,
                "max_tokens": 65536,
                "thinking_config": {"thinking_level": "HIGH"},
            },
            "noreason": {
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 16384,
                "thinking_config": {"thinking_level": "LOW"},
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
    "run_stall_timeout_secs": "CO_LLM_RUN_STALL_TIMEOUT_SECS",
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


def cap_output_tokens(settings: ModelSettings, max_tokens: int) -> ModelSettings:
    """Return a copy of *settings* with the output cap set to *max_tokens*, in lockstep.

    Centralizes the Ollama lockstep rule (otherwise only inline in the ``_LLM_SETTINGS``
    literals): Ollama honors ``max_tokens`` only at the JSON request root via ``extra_body``,
    not OpenAI's ``max_completion_tokens`` (which the scalar maps to), so the scalar and
    ``extra_body["max_tokens"]`` must move together. The ``extra_body`` mirror is set ONLY
    when ``extra_body`` already carries ``max_tokens`` (the Ollama shape); Gemini settings
    have no ``extra_body`` and get only the scalar.

    Returns a plain dict copy. ``GoogleModelSettings`` is a TypedDict whose type is erased at
    runtime regardless, and pydantic-ai consumes it via cast + ``.get()`` — so the plain-dict
    return is intentional and safe for the Google path. Does not mutate the input.
    """
    out = dict(settings)
    out["max_tokens"] = max_tokens
    if isinstance(out.get("extra_body"), dict) and "max_tokens" in out["extra_body"]:
        out["extra_body"] = {**out["extra_body"], "max_tokens": max_tokens}
    return out  # type: ignore[return-value]


def resolve_request_limit(llm: LlmSettings) -> int | None:
    """Turn-cumulative model-request cap for the SDK's UsageLimits, or None to disable.

    max_model_requests_per_turn=0 disables the cap. Single source for both the SDK
    request_limit (orchestrate._execute_run) and the final-request wrap-up nudge
    trigger (_instructions.wrap_up_prompt).
    """
    cap = llm.max_model_requests_per_turn
    return cap if cap > 0 else None


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
    # Contract pivot for Ollama context: static num_ctx in _LLM_SETTINGS must be <= max_context_tokens
    # (ceiling check); probed Modelfile num_ctx must be >= max_context_tokens (floor check).
    # Both checks use max_context_tokens as the reference — they do not compare against each other.
    max_context_tokens: int = Field(default=MAX_CONTEXT_TOKENS)
    # 0 = disabled (no cap on model requests per turn).
    max_model_requests_per_turn: int = Field(default=MAX_MODEL_REQUESTS_PER_TURN, ge=0)
    # Model-generation stall window (seconds): max wall-time the run waits for model
    # progress before giving up. Tunable because local-model latency varies widely by
    # model size and hardware; a fixed window risks false stalls on slow local setups.
    run_stall_timeout_secs: float = Field(default=RUN_STALL_TIMEOUT_SECS, gt=0)

    @model_validator(mode="after")
    def _default_model_per_provider(self) -> LlmSettings:
        if not self.model:
            self.model = DEFAULT_LLM_MODELS[self.provider]
        return self

    @model_validator(mode="after")
    def _default_context_from_profile(self) -> LlmSettings:
        if "max_context_tokens" not in self.model_fields_set:
            self.max_context_tokens = profile_max_context_tokens(resolve_model_profile(self))
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
