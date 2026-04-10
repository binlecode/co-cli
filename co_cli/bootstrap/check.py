"""System-wide integration health checks.

Data types: CheckResult, CheckItem, DoctorResult, RuntimeCheckResult.

IO check functions (called on-demand by runtime diagnostics and status display):
  check_agent_llm, check_reranker_llm, check_embedder,
  check_cross_encoder, check_ollama_model, check_mcp_server, check_tei.

Public entry points:
  check_settings(config)  — bootstrap/render_status.py (settings-level check)
  check_runtime(deps)     — tools/capabilities.py (full runtime diagnostic)

Bootstrap callers (direct, not via entry points):
  check_reranker_llm      — bootstrap/core.py (_resolve_reranker, inside _discover_knowledge_backend)
  check_cross_encoder     — bootstrap/core.py (_resolve_reranker, inside _discover_knowledge_backend)
  check_embedder          — bootstrap/core.py (_discover_knowledge_backend)

Config-shape validation lives on LlmSettings.validate_config() (config/_llm.py), not here.
"""

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from co_cli.config._core import Settings
    from co_cli.deps import CoDeps


@dataclass
class RuntimeCheckResult:
    capabilities: dict[str, Any]
    status: dict[str, Any]
    findings: list[dict[str, str]] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    mcp_probes: list[tuple[str, "CheckResult"]] = field(default_factory=list)  # bare server names

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


@dataclass
class CheckItem:
    name: str
    status: Literal["ok", "warn", "error", "skipped"]
    detail: str
    extra: str = ""


@dataclass
class DoctorResult:
    checks: list[CheckItem] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(c.status == "error" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warn" for c in self.checks)

    def by_name(self, name: str) -> CheckItem | None:
        for c in self.checks:
            if c.name == name:
                return c
        return None

    def summary_lines(self) -> list[str]:
        lines = []
        for c in self.checks:
            if c.status == "ok":
                icon = "✓"
            elif c.status == "skipped":
                icon = "·"
            elif c.status == "warn":
                icon = "⚠"
            else:
                icon = "✗"
            lines.append(f"  {icon} {c.name} — {c.detail}")
        return lines


def check_ollama_model(host: str, model: str) -> CheckResult:
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


def probe_ollama_context(host: str, model: str) -> CheckResult:
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
        detail="LLM_API_KEY not set — required for Gemini provider",
    )


def check_agent_llm(config: "Settings") -> CheckResult:
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


def check_reranker_llm(config: "Settings") -> CheckResult:
    """Check LLM reranker availability.

    Skipped if no LLM reranker is configured.
    Gemini: validates API key presence.
    Ollama: probes the reranker model specifically (not the agent reasoning model).
    """
    if config.knowledge.llm_reranker is None:
        return CheckResult(ok=True, status="skipped", detail="LLM reranker not configured")

    reranker = config.knowledge.llm_reranker
    if reranker.provider == "gemini":
        return _check_gemini_key(config.llm.api_key)

    return check_ollama_model(config.llm.host, reranker.model)


def check_embedder(config: "Settings") -> CheckResult:
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
        return check_tei(config.knowledge.embed_api_url)
    if provider == "ollama":
        return check_ollama_model(config.llm.host, config.knowledge.embedding_model)
    if provider == "gemini":
        return _check_gemini_key(config.llm.api_key)
    return CheckResult(ok=True, status="skipped", detail=f"Unknown provider: {provider}")


def check_cross_encoder(config: "Settings") -> CheckResult:
    """Check TEI cross-encoder reranker availability.

    Skipped if no cross-encoder URL is configured.
    """
    if config.knowledge.cross_encoder_reranker_url is None:
        return CheckResult(ok=True, status="skipped", detail="Cross-encoder not configured")
    return check_tei(config.knowledge.cross_encoder_reranker_url)


def check_mcp_server(command: str | None, url: str | None) -> CheckResult:
    """Check a single MCP server. Caller provides name for CheckItem mapping."""
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


def check_tei(url: str) -> CheckResult:
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


def _check_knowledge(knowledge_store: Any, backend: str) -> CheckResult:
    if knowledge_store is None:
        return CheckResult(ok=True, status="warn", detail="grep mode")
    # Probe: run a minimal health check that raises on FTS/vector schema errors.
    try:
        knowledge_store.probe()
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


def check_settings(config: "Settings") -> DoctorResult:
    """Run settings-level integration health checks.

    Checks google, obsidian, brave, and MCP servers using values from config.
    No runtime services — callers that need knowledge/skills checks use check_runtime(deps).
    """
    from co_cli.config._core import ADC_PATH, GOOGLE_TOKEN_PATH

    checks: list[CheckItem] = []

    google_result = _check_google(config.google_credentials_path, GOOGLE_TOKEN_PATH, ADC_PATH)
    checks.append(
        CheckItem(
            name="google",
            status=google_result.status,
            detail=google_result.detail,
            extra=google_result.extra.get("path", ""),
        )
    )

    obsidian_vault = str(config.obsidian_vault_path) if config.obsidian_vault_path else None
    obsidian_result = _check_obsidian(obsidian_vault)
    checks.append(
        CheckItem(
            name="obsidian",
            status=obsidian_result.status,
            detail=obsidian_result.detail,
            extra=obsidian_result.extra.get("path", ""),
        )
    )

    brave_result = _check_brave(config.brave_search_api_key)
    checks.append(CheckItem(name="brave", status=brave_result.status, detail=brave_result.detail))

    # mcp: loop over config.mcp_servers, call check_mcp_server
    for name, cfg in (config.mcp_servers or {}).items():
        result = check_mcp_server(cfg.command, cfg.url)
        checks.append(
            CheckItem(
                name=f"mcp:{name}",
                status=result.status,
                detail=result.detail,
                extra=str(result.extra.get("value", "")),
            )
        )

    return DoctorResult(checks=checks)


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
    from co_cli.config._core import ADC_PATH, GOOGLE_TOKEN_PATH

    # IO checks
    _emit_progress(progress, "Doctor: checking provider and model availability...")
    provider_result = check_agent_llm(deps.config)

    _emit_progress(progress, "Doctor: checking configured integrations...")
    google_result = _check_google(deps.config.google_credentials_path, GOOGLE_TOKEN_PATH, ADC_PATH)
    _obsidian_vault = (
        str(deps.config.obsidian_vault_path) if deps.config.obsidian_vault_path else None
    )
    obsidian_result = _check_obsidian(_obsidian_vault)
    brave_result = _check_brave(deps.config.brave_search_api_key)

    _emit_progress(progress, "Doctor: checking knowledge backend...")
    knowledge_result = _check_knowledge(deps.knowledge_store, deps.config.knowledge.search_backend)

    _emit_progress(progress, "Doctor: checking loaded skills...")
    from co_cli.commands._commands import get_skill_registry

    skills_result = _check_skills(get_skill_registry(deps.skill_commands))

    # Probe each configured MCP server via binary PATH/URL check
    mcp_probes: list[tuple[str, CheckResult]] = []
    for name, cfg in (deps.config.mcp_servers or {}).items():
        _emit_progress(progress, f"Doctor: checking MCP server '{name}'...")
        result = check_mcp_server(cfg.command, cfg.url)
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
        "reasoning_ready": bool(deps.config.llm.model),
        "google": google_result.status == "ok",
        "obsidian": obsidian_result.status == "ok",
        "brave": brave_result.status == "ok",
        "mcp_count": mcp_count,
        "knowledge_backend": deps.config.knowledge.search_backend,
        "checks": checks,
    }

    # Build source breakdown from tool_index
    tool_index = deps.tool_index
    source_counts: dict[str, int] = {}
    for tc in tool_index.values():
        source_name = tc.source.value
        source_counts[source_name] = source_counts.get(source_name, 0) + 1

    # Build status dict from session state
    status: dict[str, Any] = {
        "session_id": deps.session.session_id,
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

    # Fallbacks: active degraded-mode operations
    fallbacks: list[str] = []
    if len(deps.config.mcp_servers) == 0:
        fallbacks.append("mcp: native-only (no MCP servers configured)")

    return RuntimeCheckResult(
        capabilities=capabilities,
        status=status,
        findings=findings,
        fallbacks=fallbacks,
        mcp_probes=[(name.removeprefix("mcp:"), result) for name, result in mcp_probes],
    )
