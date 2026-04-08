"""Eval session setup: CoDeps factory, model detection, model settings."""

from typing import Any

from pydantic_ai.settings import ModelSettings

from co_cli.config._core import settings, get_settings, Settings
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend


def detect_model_tag() -> str:
    """Auto-detect a model tag from the current LLM config."""
    provider = settings.llm.provider.lower()
    entry = settings.llm.role_models.get("reasoning")
    model = entry.model if entry is not None else None
    if provider == "gemini":
        return f"gemini-{model}" if model else "gemini"
    if provider == "ollama-openai":
        return f"ollama-{model}" if model else "ollama"
    return provider


def make_eval_deps(**overrides: Any) -> CoDeps:
    """Build a CoDeps suitable for evals, pulling defaults from settings.

    Pass keyword overrides to customise any CoDeps field, e.g.
    ``make_eval_deps(brave_search_api_key=None)``.
    Service fields (shell, knowledge_store, model_registry) can
    also be passed as overrides and are extracted before building CoDeps.
    session_id is routed to CoSessionState.
    """
    s = get_settings()

    # Extract non-config fields before building CoDeps
    shell = overrides.pop("shell", ShellBackend())
    knowledge_store = overrides.pop("knowledge_store", None)
    model_registry = overrides.pop("model_registry", None)
    session_id_override = overrides.pop("session_id", "eval")
    # Discard legacy overrides that no longer map to Settings fields
    overrides.pop("mcp_servers", None)

    return CoDeps(
        shell=shell,
        knowledge_store=knowledge_store,
        model_registry=model_registry,
        config=s,
        session=CoSessionState(session_id=session_id_override),
    )


def make_eval_settings(
    model_settings: ModelSettings | None = None,
    *,
    max_tokens: int | None = None,
) -> ModelSettings:
    """Build eval settings from real model configuration.

    All values are passed through as-is from the quirks database so evals run
    against the same parameters as live sessions. Both providers now supply
    model_settings via build_agent():
      - Ollama: temperature from quirks (e.g. 0.6 for qwen3). Never override
        to 0 — thinking models produce degenerate loops at temperature=0.
      - Gemini: temperature from quirks (typically 1.0 for thinking models).
        Google's guidance: setting below 1.0 causes looping in thinking models.

    Falls back to temperature=0 only when no model settings exist at all
    (e.g. unit tests / unknown providers).

    Args:
        model_settings: Settings from build_agent(), or None for fallback.
        max_tokens: Optional override for max_tokens. Omit to use the quirks default.
    """
    if model_settings is None:
        base: dict[str, Any] = {"temperature": 0}
        if max_tokens is not None:
            base["max_tokens"] = max_tokens
        return ModelSettings(**base)

    # ModelSettings is a TypedDict — plain dict at runtime, use .get() not getattr
    base = {}
    for key in ("temperature", "top_p", "max_tokens"):
        val = model_settings.get(key)
        if val is not None:
            base[key] = val
    extra_body = model_settings.get("extra_body")
    if extra_body:
        base["extra_body"] = extra_body
    if max_tokens is not None:
        base["max_tokens"] = max_tokens
    return ModelSettings(**base)
