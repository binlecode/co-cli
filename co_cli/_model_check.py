"""Model dependency check — pre-agent gate for LLM provider and model availability.

Private helpers delegate to _probes.py for factual probing. This module retains
the PreflightResult type and run_model_check() for backward compatibility with
existing callers (_status.py). Startup policy (fail-fast gating, error emission)
lives here; probe logic lives in _probes.py.
"""

from dataclasses import dataclass, field

from co_cli.config import ModelEntry
from co_cli.deps import CoDeps
from co_cli.display import TerminalFrontend
from co_cli._probes import probe_provider, probe_role_models


@dataclass
class PreflightResult:
    ok: bool
    status: str  # "ok" | "warning" | "error"
    message: str
    # Updated role_models when _check_model_availability advances the chain.
    # Caller (run_model_check) applies this to deps.role_models. None = no change.
    role_models: dict[str, list[ModelEntry]] | None = field(default=None)


def _check_llm_provider(
    llm_provider: str,
    gemini_api_key: str | None,
    ollama_host: str,
) -> PreflightResult:
    """Check provider credentials and basic server reachability.

    Delegates to probe_provider(); maps ProbeResult → PreflightResult for callers
    that depend on the PreflightResult interface (status uses "warning" not "warn").
    """
    result = probe_provider(llm_provider, gemini_api_key, ollama_host)
    # ProbeResult uses "warn"; PreflightResult callers expect "warning"
    pf_status = "warning" if result.status == "warn" else result.status
    return PreflightResult(ok=result.ok, status=pf_status, message=result.detail)


def _check_model_availability(
    llm_provider: str,
    ollama_host: str,
    role_models: dict[str, list[ModelEntry]],
) -> PreflightResult:
    """Check Ollama model availability and return updated role_models if chains advanced.

    Delegates to probe_role_models(); maps ProbeResult → PreflightResult.
    Pure function — does not mutate role_models. Returns updated copy in result.role_models
    when chains are advanced; caller applies mutation.
    """
    result = probe_role_models(llm_provider, ollama_host, role_models)
    pf_status = "warning" if result.status == "warn" else result.status
    updated = result.extra.get("role_models")
    return PreflightResult(
        ok=result.ok,
        status=pf_status,
        message=result.detail,
        role_models=updated,
    )


def run_model_check(deps: CoDeps, frontend: TerminalFrontend) -> None:
    """Run all pre-agent model dependency checks.

    Called after create_deps() and before get_agent(). Raises RuntimeError on any error
    result (agent is never created). Reports warnings via frontend.on_status().
    """
    provider_result = _check_llm_provider(
        deps.config.llm_provider,
        deps.config.gemini_api_key,
        deps.config.ollama_host,
    )
    if provider_result.status == "error":
        raise RuntimeError(provider_result.message)
    if provider_result.status == "warning":
        frontend.on_status(f"  {provider_result.message}")

    model_result = _check_model_availability(
        deps.config.llm_provider,
        deps.config.ollama_host,
        deps.config.role_models,
    )
    if model_result.status == "error":
        raise RuntimeError(model_result.message)
    if model_result.status == "warning":
        frontend.on_status(f"  {model_result.message}")
    if model_result.role_models is not None:
        deps.config.role_models = model_result.role_models
