"""System-wide integration health checks — single source of truth for all non-LLM checks.

Provides CheckItem/DoctorResult data model, pure check_* functions, and a single
entry point run_doctor(deps) that always reads from the settings singleton for
integration config, and uses deps (when available) for runtime state checks.

Callers:
  run_doctor(deps)   — bootstrap Step 4, capabilities tool (runtime context available)
  run_doctor()       — _status.py (no runtime context; skips knowledge + skills checks)
"""

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


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


def check_google(
    credentials_path: str | None,
    token_path: Path,
    adc_path: Path,
) -> CheckItem:
    if credentials_path and os.path.exists(os.path.expanduser(credentials_path)):
        return CheckItem(
            name="google",
            status="ok",
            detail="configured (credentials file)",
            extra=credentials_path,
        )
    if token_path.exists():
        return CheckItem(
            name="google",
            status="ok",
            detail="configured (token.json)",
            extra=str(token_path),
        )
    if adc_path.exists():
        return CheckItem(
            name="google",
            status="ok",
            detail="configured (ADC)",
            extra=str(adc_path),
        )
    return CheckItem(name="google", status="warn", detail="not configured")


def check_obsidian(vault_path: Path | None) -> CheckItem:
    if vault_path is None:
        return CheckItem(name="obsidian", status="skipped", detail="not configured")
    if os.path.exists(vault_path):
        return CheckItem(
            name="obsidian",
            status="ok",
            detail="vault found",
            extra=str(vault_path),
        )
    return CheckItem(
        name="obsidian",
        status="warn",
        detail="path not found",
        extra=str(vault_path),
    )


def check_brave(api_key: str | None) -> CheckItem:
    if api_key:
        return CheckItem(name="brave", status="ok", detail="API key configured")
    return CheckItem(name="brave", status="skipped", detail="not configured")


def check_mcp_server(name: str, command: str | None, url: str | None) -> CheckItem:
    if url:
        return CheckItem(
            name=f"mcp:{name}",
            status="ok",
            detail="remote url",
            extra=url,
        )
    if command and shutil.which(command):
        return CheckItem(
            name=f"mcp:{name}",
            status="ok",
            detail=f"{command} found",
            extra=command,
        )
    cmd_label = command or "(no command)"
    return CheckItem(
        name=f"mcp:{name}",
        status="error",
        detail=f"{cmd_label} not found",
        extra=cmd_label,
    )


def check_knowledge(backend: str, index_active: bool) -> CheckItem:
    if index_active:
        return CheckItem(name="knowledge", status="ok", detail=f"{backend} active")
    return CheckItem(
        name="knowledge",
        status="warn",
        detail="grep fallback (FTS5 unavailable)",
    )


def check_skills(count: int) -> CheckItem:
    if count > 0:
        return CheckItem(name="skills", status="ok", detail=f"{count} skill(s) loaded")
    return CheckItem(name="skills", status="skipped", detail="no skills found")


def run_doctor(deps: "CoDeps | None" = None) -> DoctorResult:
    """Run integration health checks.

    Always checks: google, obsidian, brave, MCP servers (from settings singleton).
    When deps is provided: also checks knowledge index and skills (runtime state).

    Args:
        deps: CoDeps runtime context. Pass None when no agent runtime is available
              (e.g. from _status.py). Pass deps to include knowledge + skills checks.
    """
    from co_cli.config import settings
    from co_cli.tools._google_auth import GOOGLE_TOKEN_PATH, ADC_PATH

    checks: list[CheckItem] = []

    checks.append(check_google(settings.google_credentials_path, GOOGLE_TOKEN_PATH, ADC_PATH))

    obsidian_path = Path(settings.obsidian_vault_path) if settings.obsidian_vault_path else None
    checks.append(check_obsidian(obsidian_path))

    checks.append(check_brave(settings.brave_search_api_key))

    for name, cfg in (settings.mcp_servers or {}).items():
        checks.append(check_mcp_server(name, cfg.command, cfg.url))

    if deps is not None:
        checks.append(check_knowledge(
            deps.config.knowledge_search_backend,
            deps.services.knowledge_index is not None,
        ))
        checks.append(check_skills(len(deps.session.skill_registry)))

    return DoctorResult(checks=checks)
