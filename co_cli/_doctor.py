"""System-wide integration health checks — backward-compat shim over _probes.py.

Provides CheckItem/DoctorResult data model and run_doctor() entry point.
Probe logic lives in _probes.py. This module maps ProbeResult → CheckItem for callers
that depend on the DoctorResult interface.

Callers:
  run_doctor(deps)   — bootstrap Step 4, _status.py with deps
  run_doctor()       — _status.py (no runtime context; skips knowledge + skills checks)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from co_cli._probes import (
    ProbeResult,
    probe_brave,
    probe_google,
    probe_knowledge,
    probe_mcp_server,
    probe_obsidian,
    probe_skills,
)

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


def _to_check_item(name: str, result: ProbeResult) -> CheckItem:
    """Map a ProbeResult to a CheckItem for backward-compat callers."""
    return CheckItem(
        name=name,
        status=result.status,  # type: ignore[arg-type]
        detail=result.detail,
        extra=str(result.extra.get("value", "")),
    )


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

    checks.append(_to_check_item("google", probe_google(
        settings.google_credentials_path, GOOGLE_TOKEN_PATH, ADC_PATH
    )))

    obsidian_path = Path(settings.obsidian_vault_path) if settings.obsidian_vault_path else None
    checks.append(_to_check_item("obsidian", probe_obsidian(obsidian_path)))

    checks.append(_to_check_item("brave", probe_brave(settings.brave_search_api_key)))

    for name, cfg in (settings.mcp_servers or {}).items():
        checks.append(_to_check_item(f"mcp:{name}", probe_mcp_server(cfg.command, cfg.url)))

    if deps is not None:
        checks.append(_to_check_item("knowledge", probe_knowledge(
            deps.config.knowledge_search_backend,
            deps.services.knowledge_index is not None,
        )))
        checks.append(_to_check_item("skills", probe_skills(len(deps.session.skill_registry))))

    return DoctorResult(checks=checks)
