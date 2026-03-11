"""Shared factual probes — pure, side-effect-free health checks.

Each probe function checks one integration or component and returns a ProbeResult.
No startup policy, no mutation, no display — callers own those concerns.
"""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co_cli.config import ModelEntry


@dataclass
class ProbeResult:
    ok: bool
    status: str  # "ok" | "warn" | "error" | "skipped"
    detail: str
    extra: dict[str, Any] = field(default_factory=dict)


def probe_google(
    credentials_path: str | None,
    token_path: Path,
    adc_path: Path,
) -> ProbeResult:
    if credentials_path and os.path.exists(os.path.expanduser(credentials_path)):
        return ProbeResult(
            ok=True,
            status="ok",
            detail="configured (credentials file)",
            extra={"value": credentials_path},
        )
    if token_path.exists():
        return ProbeResult(
            ok=True,
            status="ok",
            detail="configured (token.json)",
            extra={"value": str(token_path)},
        )
    if adc_path.exists():
        return ProbeResult(
            ok=True,
            status="ok",
            detail="configured (ADC)",
            extra={"value": str(adc_path)},
        )
    return ProbeResult(ok=True, status="warn", detail="not configured")


def probe_obsidian(vault_path: Path | None) -> ProbeResult:
    if vault_path is None:
        return ProbeResult(ok=True, status="skipped", detail="not configured")
    if os.path.exists(vault_path):
        return ProbeResult(
            ok=True,
            status="ok",
            detail="vault found",
            extra={"value": str(vault_path)},
        )
    return ProbeResult(
        ok=True,
        status="warn",
        detail="path not found",
        extra={"value": str(vault_path)},
    )


def probe_brave(api_key: str | None) -> ProbeResult:
    if api_key:
        return ProbeResult(ok=True, status="ok", detail="API key configured")
    return ProbeResult(ok=True, status="skipped", detail="not configured")


def probe_mcp_server(command: str | None, url: str | None) -> ProbeResult:
    """Probe a single MCP server. Caller provides name for CheckItem mapping."""
    if url:
        return ProbeResult(
            ok=True,
            status="ok",
            detail="remote url",
            extra={"value": url},
        )
    if command and shutil.which(command):
        return ProbeResult(
            ok=True,
            status="ok",
            detail=f"{command} found",
            extra={"value": command},
        )
    cmd_label = command or "(no command)"
    return ProbeResult(
        ok=False,
        status="error",
        detail=f"{cmd_label} not found",
        extra={"value": cmd_label},
    )


def probe_knowledge(backend: str, index_active: bool) -> ProbeResult:
    if index_active:
        return ProbeResult(ok=True, status="ok", detail=f"{backend} active")
    return ProbeResult(ok=True, status="warn", detail="grep fallback (FTS5 unavailable)")


def probe_skills(count: int) -> ProbeResult:
    if count > 0:
        return ProbeResult(ok=True, status="ok", detail=f"{count} skill(s) loaded")
    return ProbeResult(ok=True, status="skipped", detail="no skills found")


def probe_provider(
    llm_provider: str,
    gemini_api_key: str | None,
    ollama_host: str,
) -> ProbeResult:
    """Probe LLM provider credentials and basic server reachability."""
    if llm_provider == "gemini":
        if gemini_api_key:
            return ProbeResult(ok=True, status="ok", detail="Gemini API key configured")
        return ProbeResult(
            ok=False,
            status="error",
            detail="GEMINI_API_KEY not set — required for Gemini provider",
        )

    if llm_provider == "ollama":
        try:
            import httpx
            resp = httpx.get(f"{ollama_host}/api/tags", timeout=5)
            resp.raise_for_status()
        except Exception as err:
            return ProbeResult(
                ok=True,
                status="warn",
                detail=f"Ollama model check skipped — {err}",
            )

    if not gemini_api_key:
        return ProbeResult(
            ok=True,
            status="warn",
            detail="Gemini API key not set — Gemini-dependent features unavailable",
        )

    return ProbeResult(ok=True, status="ok", detail="Provider configured")


def probe_role_models(
    llm_provider: str,
    ollama_host: str,
    role_models: "dict[str, list[ModelEntry]]",
) -> ProbeResult:
    """Probe Ollama model availability; returns updated role_models in extra when chains advance."""
    if llm_provider != "ollama":
        return ProbeResult(
            ok=True,
            status="ok",
            detail="Model availability check skipped (non-Ollama provider)",
        )

    try:
        import httpx
        resp = httpx.get(f"{ollama_host}/api/tags", timeout=5)
        resp.raise_for_status()
        installed = {m["name"] for m in resp.json().get("models", [])}
    except Exception as err:
        return ProbeResult(
            ok=True,
            status="warn",
            detail=f"Ollama model check skipped — {err}",
        )

    updated_roles: dict[str, list[ModelEntry]] = {k: list(v) for k, v in role_models.items()}
    chain_changed = False
    status_messages: list[str] = []

    reasoning_chain = updated_roles.get("reasoning", [])
    if reasoning_chain:
        available_reasoning = [e for e in reasoning_chain if e.model in installed]
        if not available_reasoning:
            return ProbeResult(
                ok=False,
                status="error",
                detail="No reasoning model available — check role_models.reasoning",
            )
        if available_reasoning != reasoning_chain:
            updated_roles["reasoning"] = available_reasoning
            chain_changed = True
            status_messages.append(
                f"Reasoning model → {available_reasoning[0].model} (chain advanced)"
            )

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
        return ProbeResult(
            ok=True,
            status="warn",
            detail="; ".join(status_messages) if status_messages else "Model chains advanced",
            extra={"role_models": updated_roles},
        )

    return ProbeResult(ok=True, status="ok", detail="All models available")
