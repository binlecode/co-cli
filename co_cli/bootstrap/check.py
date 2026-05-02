"""System-wide integration health checks.

Data types: CheckResult, RuntimeCheckResult.

Public entry point:
  check_runtime(deps)     — tools/capabilities.py (full runtime diagnostic)

All individual IO check functions are package-private (underscore prefix).
Config-shape validation lives on LlmSettings.validate_config() (config/_llm.py), not here.
"""

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from co_cli.config.core import Settings
    from co_cli.deps import CoDeps


# Map raw CheckResult.status to a coarse component-state vocabulary used by the
# self-check tool display. "warn" is split below based on intent: soft-unconfigured
# integrations (google/brave/obsidian/skills with no config) report "not_configured",
# genuine runtime failures report "degraded".
_STATE_BY_STATUS: dict[str, str] = {
    "ok": "available",
    "skipped": "not_configured",
    "warn": "degraded",
    "error": "unavailable",
}


@dataclass
class RuntimeCheckResult:
    capabilities: dict[str, Any]
    status: dict[str, Any]
    findings: list[dict[str, str]] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    mcp_probes: list[tuple[str, "CheckResult"]] = field(default_factory=list)  # bare server names
    component_status: list[dict[str, str]] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        for f in self.findings:
            severity = f.get("severity", "error")
            icon = "✗" if severity == "error" else "⚠"
            lines.append(f"  {icon} {f['component']} — {f['issue']}")
        for fb in self.fallbacks:
            lines.append(f"  · {fb}")
        if not lines:
            lines.append("  ✓ All systems operational")
        return lines


@dataclass
class CheckResult:
    ok: bool
    status: Literal["ok", "warn", "error", "skipped"]
    detail: str
    extra: dict[str, Any] = field(default_factory=dict)


def _check_ollama_model(host: str, model: str) -> CheckResult:
    """Check a single Ollama model by querying /api/tags.

    Unreachable host → warn (Ollama may still be starting; caller decides impact).
    Model absent     → error (hard fail; caller must degrade).
    Model present    → ok.
    """
    try:
        import httpx

        resp = httpx.get(f"{host}/api/tags", timeout=5)
        resp.raise_for_status()
        installed = {m["name"] for m in resp.json().get("models", [])}
    except Exception as err:
        return CheckResult(ok=True, status="warn", detail=f"Ollama check skipped — {err}")

    if model not in installed:
        return CheckResult(ok=False, status="error", detail=f"Model not available: {model}")
    return CheckResult(ok=True, status="ok", detail=f"Model available: {model}")


# Minimum context window for agentic tool-use sessions.
# Below this, system prompt + tools (~5.5K) + working history (~20K) +
# one tool result (~5K) + compaction headroom (13K) + output reserve (~16K)
# + safety margin (~5.5K) cannot fit. The agent would compact every turn,
# and the summarizer call itself would consume most of the remaining context.
MIN_AGENTIC_CONTEXT = 65_536


def _probe_ollama_context(host: str, model: str) -> CheckResult:
    """Probe Ollama /api/show for the model's runtime num_ctx.

    Returns CheckResult with extra={"num_ctx": N} on success.
    The num_ctx value comes from the Modelfile's PARAMETER section —
    this is the actual runtime allocation, not the model architecture's
    theoretical maximum (model_info.*.context_length).

    Fail-fast: if num_ctx < MIN_AGENTIC_CONTEXT (64K), returns error
    with actionable remediation message.

    Unreachable host or missing model → warn (caller decides impact).
    """
    try:
        import httpx

        resp = httpx.post(f"{host}/api/show", json={"model": model}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as err:
        return CheckResult(
            ok=True,
            status="warn",
            detail=f"Ollama context probe skipped — {err}",
        )

    # Parse num_ctx from the parameters string (Modelfile values)
    num_ctx = 0
    params_str = data.get("parameters", "")
    for line in params_str.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "num_ctx":
            try:
                num_ctx = int(parts[1])
            except ValueError:
                pass
            break

    if num_ctx <= 0:
        return CheckResult(
            ok=True,
            status="warn",
            detail=f"Could not read num_ctx from Modelfile for {model}",
        )

    if num_ctx < MIN_AGENTIC_CONTEXT:
        return CheckResult(
            ok=False,
            status="error",
            detail=(
                f"Model {model} has num_ctx={num_ctx:,} — minimum for agentic "
                f"tool use is {MIN_AGENTIC_CONTEXT:,} (64K). "
                f"Update your Modelfile: PARAMETER num_ctx {MIN_AGENTIC_CONTEXT}"
            ),
            extra={"num_ctx": num_ctx},
        )

    return CheckResult(
        ok=True,
        status="ok",
        detail=f"Runtime num_ctx={num_ctx:,}",
        extra={"num_ctx": num_ctx},
    )


def _check_gemini_key(api_key: str | None) -> CheckResult:
    if api_key:
        return CheckResult(ok=True, status="ok", detail="Gemini API key configured")
    return CheckResult(
        ok=False,
        status="error",
        detail="CO_LLM_API_KEY not set — required for Gemini provider",
    )


def _check_agent_llm(config: "Settings") -> CheckResult:
    """Check session agent LLM credentials and model availability in one pass.

    Gemini: validates API key presence (no HTTP call).
    Ollama: one GET /api/tags call — checks reachability and configured model name.
      - Unreachable → warn (soft fail; session continues)
      - Model missing → error (hard fail; session cannot start)
    """
    if config.llm.uses_gemini():
        return _check_gemini_key(config.llm.api_key)

    try:
        import httpx

        resp = httpx.get(f"{config.llm.host}/api/tags", timeout=5)
        resp.raise_for_status()
        installed = {m["name"] for m in resp.json().get("models", [])}
    except Exception as err:
        return CheckResult(
            ok=True,
            status="warn",
            detail=f"Ollama check skipped — {err}",
            extra={"reason": "unreachable"},
        )

    if config.llm.model not in installed:
        return CheckResult(
            ok=False,
            status="error",
            detail=f"Model not available: {config.llm.model}",
        )

    return CheckResult(ok=True, status="ok", detail="Provider and model configured")


def _check_embedder(config: "Settings") -> CheckResult:
    """Check embedding provider availability.

    Skipped if provider is "none".
    TEI: HTTP GET probe to embed API URL.
    Ollama: probes the configured embedding model.
    Gemini: validates API key presence.
    """
    provider = config.knowledge.embedding_provider
    if provider == "none":
        return CheckResult(ok=True, status="skipped", detail="Embedding provider is 'none'")
    if provider == "tei":
        return _check_tei(config.knowledge.embed_api_url)
    if provider == "ollama":
        return _check_ollama_model(config.llm.host, config.knowledge.embedding_model)
    if provider == "gemini":
        return _check_gemini_key(config.llm.api_key)
    return CheckResult(ok=True, status="skipped", detail=f"Unknown provider: {provider}")


def _check_cross_encoder(config: "Settings") -> CheckResult:
    """Check TEI cross-encoder reranker availability.

    Skipped if no cross-encoder URL is configured.
    """
    if config.knowledge.cross_encoder_reranker_url is None:
        return CheckResult(ok=True, status="skipped", detail="Cross-encoder not configured")
    return _check_tei(config.knowledge.cross_encoder_reranker_url)


def _check_mcp_server(command: str | None, url: str | None) -> CheckResult:
    """Check a single MCP server."""
    if url:
        return CheckResult(
            ok=True,
            status="ok",
            detail="remote url",
            extra={"value": url},
        )
    if command and shutil.which(command):
        return CheckResult(
            ok=True,
            status="ok",
            detail=f"{command} found",
            extra={"value": command},
        )
    cmd_label = command or "(no command)"
    return CheckResult(
        ok=False,
        status="error",
        detail=f"{cmd_label} not found",
        extra={"value": cmd_label},
    )


def _check_tei(url: str) -> CheckResult:
    """Check a TEI service (embed or rerank) by GET /info.

    Returns server metadata (model_id, max_client_batch_size, etc.) in extra.
    """
    try:
        import httpx

        resp = httpx.get(f"{url.rstrip('/')}/info", timeout=2)
        resp.raise_for_status()
        info = resp.json()
        return CheckResult(
            ok=True,
            status="ok",
            detail=f"reachable at {url}",
            extra=info,
        )
    except Exception as err:
        return CheckResult(ok=False, status="error", detail=f"not reachable — {err}")


def _check_google(creds: str | None, token_path: Path, adc_path: Path) -> CheckResult:
    if creds and os.path.exists(os.path.expanduser(creds)):
        return CheckResult(
            ok=True, status="ok", detail="configured (credentials file)", extra={"path": creds}
        )
    if token_path.exists():
        return CheckResult(
            ok=True, status="ok", detail="configured (token.json)", extra={"path": str(token_path)}
        )
    if adc_path.exists():
        return CheckResult(
            ok=True, status="ok", detail="configured (ADC)", extra={"path": str(adc_path)}
        )
    return CheckResult(ok=True, status="warn", detail="not configured")


def _check_obsidian(vault: str | None) -> CheckResult:
    if vault is None:
        return CheckResult(ok=True, status="skipped", detail="not configured")
    if os.path.exists(vault):
        return CheckResult(ok=True, status="ok", detail="vault found", extra={"path": vault})
    return CheckResult(ok=True, status="warn", detail="path not found", extra={"path": vault})


def _check_brave(api_key: str | None) -> CheckResult:
    if api_key:
        return CheckResult(ok=True, status="ok", detail="API key configured")
    return CheckResult(ok=True, status="skipped", detail="not configured")


def _check_memory_store(memory_store: Any, backend: str) -> CheckResult:
    if memory_store is None:
        return CheckResult(ok=True, status="warn", detail="grep mode")
    # Probe: run a minimal health check that raises on FTS/vector schema errors.
    try:
        memory_store.probe()
    except Exception as e:
        return CheckResult(ok=False, status="error", detail=str(e))
    return CheckResult(ok=True, status="ok", detail=f"{backend} active")


def _check_skills(skill_registry: list[dict]) -> CheckResult:
    skill_count = len(skill_registry)
    return CheckResult(
        ok=True,
        status="ok" if skill_count > 0 else "skipped",
        detail=f"{skill_count} skill(s) loaded" if skill_count > 0 else "no skills found",
    )


def _emit_progress(
    progress: Callable[[str], None] | None,
    message: str,
) -> None:
    if progress is not None:
        progress(message)


def check_runtime(
    deps: "CoDeps",
    *,
    progress: Callable[[str], None] | None = None,
) -> "RuntimeCheckResult":
    """Assemble a full runtime diagnostic snapshot.

    Calls IO check functions and inlines trivial checks, combines capabilities from
    config and integration state with session state from deps. No startup policy —
    failures are recorded as findings, not raised as exceptions.
    """
    from co_cli.config.core import ADC_PATH, GOOGLE_TOKEN_PATH

    # IO checks
    _emit_progress(progress, "Doctor: checking provider and model availability...")
    provider_result = _check_agent_llm(deps.config)

    _emit_progress(progress, "Doctor: checking configured integrations...")
    google_result = _check_google(deps.config.google_credentials_path, GOOGLE_TOKEN_PATH, ADC_PATH)
    _obsidian_vault = (
        str(deps.config.obsidian_vault_path) if deps.config.obsidian_vault_path else None
    )
    obsidian_result = _check_obsidian(_obsidian_vault)
    brave_result = _check_brave(deps.config.brave_search_api_key)

    _emit_progress(progress, "Doctor: checking knowledge backend...")
    knowledge_result = _check_memory_store(deps.memory_store, deps.config.knowledge.search_backend)

    _emit_progress(progress, "Doctor: checking loaded skills...")
    from co_cli.skills.registry import get_skill_registry

    skills_result = _check_skills(get_skill_registry(deps.skill_commands))

    # Probe each configured MCP server via binary PATH/URL check
    mcp_probes: list[tuple[str, CheckResult]] = []
    for name, cfg in (deps.config.mcp_servers or {}).items():
        _emit_progress(progress, f"Doctor: checking MCP server '{name}'...")
        result = _check_mcp_server(cfg.command, cfg.url)
        mcp_probes.append((f"mcp:{name}", result))
    mcp_count = sum(1 for _, r in mcp_probes if r.ok)

    # Assemble named check list for checks display and findings scan
    named_checks: list[tuple[str, CheckResult]] = [
        ("provider", provider_result),
        ("google", google_result),
        ("obsidian", obsidian_result),
        ("brave", brave_result),
        ("knowledge", knowledge_result),
        ("skills", skills_result),
        *mcp_probes,
    ]
    checks = [{"name": name, "status": r.status, "detail": r.detail} for name, r in named_checks]

    # Build capabilities dict
    capabilities: dict[str, Any] = {
        "provider": {
            "ok": provider_result.ok,
            "status": provider_result.status,
            "detail": provider_result.detail,
        },
        "reasoning_model": deps.config.llm.model,
        "reasoning_ready": provider_result.ok,
        "google": google_result.status == "ok",
        "obsidian": obsidian_result.status == "ok",
        "brave": brave_result.status == "ok",
        "mcp_count": mcp_count,
        "knowledge_backend": deps.config.knowledge.search_backend,
        "checks": checks,
    }

    # Build status dict from session state
    tool_index = deps.tool_index
    source_counts: dict[str, int] = {}
    for tc in tool_index.values():
        source_name = tc.source.value
        source_counts[source_name] = source_counts.get(source_name, 0) + 1

    status: dict[str, Any] = {
        "session_id": deps.session.session_path.stem[-8:],
        "active_skill": deps.runtime.active_skill_name,
        "tool_names": list(tool_index.keys()),
        "tool_approvals": {name: tc.approval for name, tc in tool_index.items()},
        "tool_count": len(tool_index),
        "skill_count": len(get_skill_registry(deps.skill_commands)),
        "mcp_mode": "mcp" if len(deps.config.mcp_servers) > 0 else "native-only",
        "knowledge_mode": deps.config.knowledge.search_backend,
        "source_counts": source_counts,
    }

    # Findings: checks that returned a non-ok status (error or warn)
    findings: list[dict[str, str]] = []
    for name, result in named_checks:
        if result.status not in ("ok", "skipped"):
            findings.append(
                {
                    "component": name,
                    "issue": result.detail,
                    "severity": "warn" if result.status == "warn" else "error",
                }
            )

    # Component status: coarse available / not_configured / degraded / unavailable
    # vocabulary for the self-check surface. "warn" for unconfigured integrations
    # (google, brave) collapses to "not_configured"; true failures stay "degraded".
    component_status: list[dict[str, str]] = []
    for name, result in named_checks:
        state = _STATE_BY_STATUS.get(result.status, "degraded")
        if state == "degraded" and "not configured" in result.detail.lower():
            state = "not_configured"
        component_status.append(
            {"component": name, "state": state, "detail": result.detail},
        )

    # Fallbacks: normalized from deps.degradations (bootstrap-recorded runtime fallbacks)
    fallbacks: list[str] = []
    for key, detail in sorted(deps.degradations.items()):
        if key == "knowledge":
            fallbacks.append(f"knowledge: {detail}")
        elif key.startswith("mcp."):
            server = key.removeprefix("mcp.")
            fallbacks.append(f"mcp.{server}: tool discovery failed — {detail}")
        else:
            fallbacks.append(f"{key}: {detail}")

    return RuntimeCheckResult(
        capabilities=capabilities,
        status=status,
        findings=findings,
        fallbacks=fallbacks,
        mcp_probes=[(name.removeprefix("mcp:"), result) for name, result in mcp_probes],
        component_status=component_status,
    )
