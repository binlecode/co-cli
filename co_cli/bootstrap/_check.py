"""System-wide integration health checks.

Provides CheckResult (IO check return type), CheckItem/DoctorResult data model,
IO check functions (check_llm, check_mcp_server, check_tei),
and the check_settings() entry point.

Callers:
  check_settings(config)  — bootstrap/_render_status.py (settings-level check; no runtime services)
  check_runtime(deps)     — bootstrap/_bootstrap.py, tools/capabilities.py (full runtime diagnostic)
"""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from co_cli.deps import CoDeps, CoConfig

from co_cli.config import ROLE_REASONING, ROLE_SUMMARIZATION, ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS


@dataclass
class RuntimeCheck:
    capabilities: dict[str, Any]
    status: dict[str, Any]
    findings: list[dict[str, str]] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)

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


def check_llm(config: "CoConfig") -> CheckResult:
    """Check LLM provider credentials and model availability in one pass.

    Gemini: validates API key presence (no HTTP call).
    Ollama: one GET /api/tags call — checks reachability and all configured model names.
      - Unreachable → warn (soft fail; session continues)
      - Reasoning model missing → error (hard fail; session cannot start)
      - Optional role model missing → warn (soft fail; those tools degrade silently)
    """
    if config.uses_gemini():
        if config.llm_api_key:
            return CheckResult(ok=True, status="ok", detail="Gemini API key configured")
        return CheckResult(
            ok=False,
            status="error",
            detail="LLM_API_KEY not set — required for Gemini provider",
        )

    try:
        import httpx
        resp = httpx.get(f"{config.llm_host}/api/tags", timeout=5)
        resp.raise_for_status()
        installed = {m["name"] for m in resp.json().get("models", [])}
    except Exception as err:
        return CheckResult(ok=True, status="warn", detail=f"Ollama check skipped — {err}")

    reasoning_entry = config.role_models.get(ROLE_REASONING)
    if reasoning_entry and reasoning_entry.model not in installed:
        return CheckResult(
            ok=False,
            status="error",
            detail=f"Reasoning model not available: {reasoning_entry.model}",
        )

    missing_optional: list[str] = []
    for role in (ROLE_SUMMARIZATION, ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS):
        entry = config.role_models.get(role)
        if entry and entry.model not in installed:
            missing_optional.append(f"{role}: {entry.model}")

    if missing_optional:
        return CheckResult(
            ok=True,
            status="warn",
            detail=f"Optional roles have unavailable models: {'; '.join(missing_optional)}",
        )

    return CheckResult(ok=True, status="ok", detail="Provider and models configured")


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
    """Check a TEI service (embed or rerank) by GET to its base URL."""
    try:
        import httpx
        httpx.get(url, timeout=1)
        return CheckResult(ok=True, status="ok", detail=f"reachable at {url}")
    except Exception as err:
        return CheckResult(ok=False, status="error", detail=f"not reachable — {err}")


def _check_google(creds: str | None, token_path: Path, adc_path: Path) -> CheckResult:
    if creds and os.path.exists(os.path.expanduser(creds)):
        return CheckResult(ok=True, status="ok", detail="configured (credentials file)", extra={"path": creds})
    if token_path.exists():
        return CheckResult(ok=True, status="ok", detail="configured (token.json)", extra={"path": str(token_path)})
    if adc_path.exists():
        return CheckResult(ok=True, status="ok", detail="configured (ADC)", extra={"path": str(adc_path)})
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


def _check_knowledge(knowledge_index: Any, backend: str) -> CheckResult:
    index_active = knowledge_index is not None
    return CheckResult(
        ok=True,
        status="ok" if index_active else "warn",
        detail=f"{backend} active" if index_active else "grep mode",
    )


def _check_skills(skill_registry: dict) -> CheckResult:
    skill_count = len(skill_registry)
    return CheckResult(
        ok=True,
        status="ok" if skill_count > 0 else "skipped",
        detail=f"{skill_count} skill(s) loaded" if skill_count > 0 else "no skills found",
    )


def check_settings(config: "CoConfig") -> DoctorResult:
    """Run settings-level integration health checks.

    Checks google, obsidian, brave, and MCP servers using values from config.
    No runtime services — callers that need knowledge/skills checks use check_runtime(deps).
    """
    from co_cli.config import GOOGLE_TOKEN_PATH, ADC_PATH

    checks: list[CheckItem] = []

    google_result = _check_google(config.google_credentials_path, GOOGLE_TOKEN_PATH, ADC_PATH)
    checks.append(CheckItem(
        name="google",
        status=google_result.status,
        detail=google_result.detail,
        extra=google_result.extra.get("path", ""),
    ))

    obsidian_vault = str(config.obsidian_vault_path) if config.obsidian_vault_path else None
    obsidian_result = _check_obsidian(obsidian_vault)
    checks.append(CheckItem(
        name="obsidian",
        status=obsidian_result.status,
        detail=obsidian_result.detail,
        extra=obsidian_result.extra.get("path", ""),
    ))

    brave_result = _check_brave(config.brave_search_api_key)
    checks.append(CheckItem(name="brave", status=brave_result.status, detail=brave_result.detail))

    # mcp: loop over config.mcp_servers, call check_mcp_server
    for name, cfg in (config.mcp_servers or {}).items():
        result = check_mcp_server(cfg.command, cfg.url)
        checks.append(CheckItem(
            name=f"mcp:{name}",
            status=result.status,
            detail=result.detail,
            extra=str(result.extra.get("value", "")),
        ))

    return DoctorResult(checks=checks)


def check_runtime(deps: "CoDeps", *, skip_provider_checks: bool = False) -> "RuntimeCheck":
    """Assemble a full runtime diagnostic snapshot.

    Calls IO check functions and inlines trivial checks, combines capabilities from
    config and integration state with session state from deps. No startup policy —
    failures are recorded as findings, not raised as exceptions.

    skip_provider_checks: when True, substitute a static ok result for check_llm
        (avoids a duplicate HTTP call when bootstrap already validated the provider at startup).
    """
    from co_cli.config import ADC_PATH, GOOGLE_TOKEN_PATH

    # IO checks
    if skip_provider_checks:
        provider_result = CheckResult(ok=True, status="ok", detail="validated at startup")
    else:
        provider_result = check_llm(deps.config)

    google_result = _check_google(deps.config.google_credentials_path, GOOGLE_TOKEN_PATH, ADC_PATH)
    obsidian_result = _check_obsidian(deps.config.obsidian_vault_path)
    brave_result = _check_brave(deps.config.brave_search_api_key)
    knowledge_result = _check_knowledge(deps.services.knowledge_index, deps.config.knowledge_search_backend)
    skills_result = _check_skills(deps.session.skill_registry)

    # Probe each configured MCP server; count live ones
    mcp_probes: list[tuple[str, CheckResult]] = []
    for name, cfg in (deps.config.mcp_servers or {}).items():
        mcp_probes.append((f"mcp:{name}", check_mcp_server(cfg.command, cfg.url)))
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
    checks = [
        {"name": name, "status": r.status, "detail": r.detail}
        for name, r in named_checks
    ]

    # Build capabilities dict
    reasoning_entry = deps.config.role_models.get(ROLE_REASONING)
    capabilities: dict[str, Any] = {
        "provider": {
            "ok": provider_result.ok,
            "status": provider_result.status,
            "detail": provider_result.detail,
        },
        "reasoning_model": reasoning_entry.model if reasoning_entry else None,
        "reasoning_ready": reasoning_entry is not None,
        "google": google_result.status == "ok",
        "obsidian": obsidian_result.status == "ok",
        "brave": brave_result.status == "ok",
        "mcp_count": mcp_count,
        "knowledge_backend": deps.config.knowledge_search_backend,
        "checks": checks,
    }

    # Build status dict from session state
    status: dict[str, Any] = {
        "session_id": deps.session.session_id,
        "active_skill": deps.session.active_skill_name,
        "skill_grants": sorted(deps.session.skill_tool_grants),
        "tool_names": list(deps.session.tool_names),
        "tool_approvals": dict(deps.session.tool_approvals),
        "tool_count": len(deps.session.tool_names),
        "skill_count": len(deps.session.skill_registry),
        "mcp_mode": "mcp" if len(deps.config.mcp_servers) > 0 else "native-only",
        "knowledge_mode": deps.config.knowledge_search_backend,
    }

    # Findings: checks that returned ok=False
    findings: list[dict[str, str]] = []
    for name, result in named_checks:
        if not result.ok:
            findings.append({
                "component": name,
                "issue": result.detail,
                "severity": "error" if result.status == "error" else "warn",
            })

    # Fallbacks: active degraded-mode operations
    fallbacks: list[str] = []
    if len(deps.config.mcp_servers) == 0:
        fallbacks.append("mcp: native-only (no MCP servers configured)")

    return RuntimeCheck(
        capabilities=capabilities,
        status=status,
        findings=findings,
        fallbacks=fallbacks,
    )
