"""Eval session setup: CoDeps factory, model detection, model settings."""

from typing import Any

from pydantic_ai.settings import ModelSettings

from co_cli.config import settings, get_settings
from co_cli.deps import CoDeps, CoServices, CoConfig, CoSessionState
from co_cli.tools._shell_backend import ShellBackend


def detect_model_tag() -> str:
    """Auto-detect a model tag from the current LLM config."""
    provider = settings.llm_provider.lower()
    models = settings.role_models.get("reasoning", [])
    model = models[0] if models else None
    if provider == "gemini":
        return f"gemini-{model}" if model else "gemini"
    if provider == "ollama":
        return f"ollama-{model}" if model else "ollama"
    return provider


def make_eval_deps(**overrides: Any) -> CoDeps:
    """Build a CoDeps suitable for evals, pulling defaults from settings.

    Pass keyword overrides to customise any CoConfig field, e.g.
    ``make_eval_deps(brave_search_api_key=None)``.
    Service fields (shell, knowledge_index, task_runner) and session fields
    (skill_registry, session_id) can also be passed as overrides and are
    extracted before building CoConfig. session_id is routed to CoSessionState.
    """
    s = get_settings()

    # Extract non-config fields before building CoConfig
    shell = overrides.pop("shell", ShellBackend())
    knowledge_index = overrides.pop("knowledge_index", None)
    task_runner = overrides.pop("task_runner", None)
    model_registry = overrides.pop("model_registry", None)
    skill_registry = overrides.pop("skill_registry", [])
    session_id_override = overrides.pop("session_id", "eval")

    config_defaults: dict[str, Any] = {
        "obsidian_vault_path": None,
        "google_credentials_path": None,
        "shell_safe_commands": [],
        "brave_search_api_key": s.brave_search_api_key,
        "web_policy": s.web_policy,
        "web_http_max_retries": s.web_http_max_retries,
        "web_http_backoff_base_seconds": s.web_http_backoff_base_seconds,
        "web_http_backoff_max_seconds": s.web_http_backoff_max_seconds,
        "web_http_jitter_ratio": s.web_http_jitter_ratio,
        "doom_loop_threshold": s.doom_loop_threshold,
        "max_reflections": s.max_reflections,
        "memory_max_count": s.memory_max_count,
        "memory_dedup_window_days": s.memory_dedup_window_days,
        "memory_dedup_threshold": s.memory_dedup_threshold,
        "max_history_messages": s.max_history_messages,
        "tool_output_trim_chars": s.tool_output_trim_chars,
        "knowledge_reranker_provider": s.knowledge_reranker_provider,
        "role_models": dict(s.role_models),
        "llm_provider": s.llm_provider,
        "llm_host": s.llm_host,
        "llm_num_ctx": s.llm_num_ctx,
    }
    config_defaults.update(overrides)

    return CoDeps(
        services=CoServices(
            shell=shell,
            knowledge_index=knowledge_index,
            task_runner=task_runner,
            model_registry=model_registry,
        ),
        config=CoConfig(**config_defaults),
        session=CoSessionState(session_id=session_id_override, skill_registry=skill_registry),
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
