"""Model dependency check — pre-agent gate for LLM provider and model availability."""

from dataclasses import dataclass, field

from co_cli.config import ModelEntry
from co_cli.deps import CoDeps
from co_cli.display import TerminalFrontend


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

    Priority:
    1. Gemini provider without API key → error (cannot proceed).
    2. Ollama provider unreachable → warning (soft fail; model list check will also skip).
    3. Non-Gemini provider without Gemini key → warning (Gemini-dependent features unavailable).
    4. All checks pass → ok.
    """
    if llm_provider == "gemini":
        if gemini_api_key:
            return PreflightResult(ok=True, status="ok", message="Gemini API key configured")
        return PreflightResult(
            ok=False,
            status="error",
            message="GEMINI_API_KEY not set — required for Gemini provider",
        )

    if llm_provider == "ollama":
        try:
            import httpx
            resp = httpx.get(f"{ollama_host}/api/tags", timeout=5)
            resp.raise_for_status()
        except Exception as err:
            return PreflightResult(
                ok=True,
                status="warning",
                message=f"Ollama model check skipped — {err}",
            )

    if not gemini_api_key:
        return PreflightResult(
            ok=True,
            status="warning",
            message="Gemini API key not set — Gemini-dependent features unavailable",
        )

    return PreflightResult(ok=True, status="ok", message="Provider configured")


def _check_model_availability(
    llm_provider: str,
    ollama_host: str,
    role_models: dict[str, list[ModelEntry]],
) -> PreflightResult:
    """Check Ollama model availability and return updated role_models if chains advanced.

    Ollama-only; returns ok immediately for non-Ollama providers.
    Pure function — does not mutate role_models. Returns updated copy in result.role_models
    when chains are advanced; caller applies mutation.
    """
    if llm_provider != "ollama":
        return PreflightResult(
            ok=True,
            status="ok",
            message="Model availability check skipped (non-Ollama provider)",
        )

    try:
        import httpx
        resp = httpx.get(f"{ollama_host}/api/tags", timeout=5)
        resp.raise_for_status()
        installed = {m["name"] for m in resp.json().get("models", [])}
    except Exception as err:
        return PreflightResult(
            ok=True,
            status="warning",
            message=f"Ollama model check skipped — {err}",
        )

    updated_roles: dict[str, list[ModelEntry]] = {k: list(v) for k, v in role_models.items()}
    chain_changed = False
    status_messages: list[str] = []

    reasoning_chain = updated_roles.get("reasoning", [])
    if reasoning_chain:
        available_reasoning = [e for e in reasoning_chain if e.model in installed]
        if not available_reasoning:
            return PreflightResult(
                ok=False,
                status="error",
                message="No reasoning model available — check role_models.reasoning",
            )
        if available_reasoning != reasoning_chain:
            updated_roles["reasoning"] = available_reasoning
            chain_changed = True
            status_messages.append(f"Reasoning model → {available_reasoning[0].model} (chain advanced)")

    for role in ("summarization", "coding", "research", "analysis"):
        chain = updated_roles.get(role, [])
        if not chain:
            continue
        available = [e for e in chain if e.model in installed]
        if not available:
            updated_roles[role] = []
            chain_changed = True
            status_messages.append(f"{role} role disabled — no models available")
        elif available != chain:
            updated_roles[role] = available
            chain_changed = True
            status_messages.append(f"{role} chain advanced to: {available[0].model}")

    if chain_changed:
        return PreflightResult(
            ok=True,
            status="warning",
            message="; ".join(status_messages) if status_messages else "Model chains advanced",
            role_models=updated_roles,
        )

    return PreflightResult(ok=True, status="ok", message="All models available")


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
