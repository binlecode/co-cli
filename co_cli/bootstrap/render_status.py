"""Environment / health checks and status table rendering."""

import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from rich.table import Table

from co_cli.bootstrap.check import check_agent_llm, check_settings
from co_cli.config._core import LOGS_DB, USER_DIR, Settings
from co_cli.display._core import console

_PYPROJECT = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"


@dataclass
class StatusResult:
    version: str
    git_branch: str | None
    cwd: str  # basename
    shell: str  # "subprocess (approval-gated)"
    llm_provider: str  # "Gemini (model)" | "Ollama (model)"
    llm_status: str  # "configured" | "online" | "offline" | "missing key"
    google: str  # "configured" | "adc" | "not found"
    google_detail: str
    obsidian: str  # "configured" | "not found"
    web_search: str  # "configured" | "not configured"
    mcp_servers: list[tuple[str, str, bool]]  # [(name, status, approval_required), ...]
    tool_count: int
    db_size: str  # "1.2 KB" | "0 KB"
    obsidian_vault_path: str | None = None


def _resolve_llm_status(config: Settings) -> tuple[str, str]:
    """Return (llm_provider_str, llm_status_str) for the configured LLM."""
    provider = config.llm.provider.lower()
    model = config.llm.model
    if not model:
        return f"{provider.title()} (no model configured)", "misconfigured"
    if provider == "gemini":
        provider_check = check_agent_llm(config)
        return (
            f"Gemini ({model})",
            "configured" if provider_check.status == "ok" else "missing key",
        )
    provider_check = check_agent_llm(config)
    if provider_check.status == "error":
        return f"Ollama ({model})", "misconfigured"
    if provider_check.status == "warn":
        reason = provider_check.extra.get("reason")
        return f"Ollama ({model})", "offline" if reason == "unreachable" else "online"
    return f"Ollama ({model})", "online"


def get_status(config: Settings, tool_count: int = 0) -> StatusResult:
    """Gather system status into a plain dataclass (no display side-effects)."""

    # -- version --
    version = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]

    # -- git branch --
    try:
        git_branch: str | None = (
            subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        git_branch = None

    # -- cwd --
    cwd = Path.cwd().name

    # -- shell --
    shell = "subprocess (approval-gated)"

    # -- llm --
    llm_provider, llm_status = _resolve_llm_status(config)

    # -- integrations via check_settings --
    doctor = check_settings(config)

    google_item = doctor.by_name("google")
    if google_item and google_item.status == "ok":
        google = "adc" if "ADC" in google_item.detail else "configured"
        google_detail = google_item.extra
    else:
        google = "not found"
        google_detail = "Run 'co chat' to auto-setup or install gcloud"

    obsidian_item = doctor.by_name("obsidian")
    if obsidian_item and obsidian_item.status == "ok":
        obsidian = "configured"
    elif obsidian_item and obsidian_item.status == "skipped":
        obsidian = "not configured"
    else:
        obsidian = "not found"

    brave_item = doctor.by_name("brave")
    web_search = "configured" if brave_item and brave_item.status == "ok" else "not configured"

    mcp_status: list[tuple[str, str, bool]] = []
    for item in doctor.checks:
        if not item.name.startswith("mcp:"):
            continue
        server_name = item.name[4:]
        if item.detail == "remote url":
            status_str = "remote (url)"
        elif item.status == "ok":
            status_str = "ready"
        else:
            status_str = item.detail
        mcp_cfg = config.mcp_servers.get(server_name)
        approval_required = mcp_cfg.approval == "ask" if mcp_cfg else False
        mcp_status.append((server_name, status_str, approval_required))

    # -- db size --
    db_size = f"{os.path.getsize(LOGS_DB) / 1024:.1f} KB" if LOGS_DB.exists() else "0 KB"

    return StatusResult(
        version=version,
        git_branch=git_branch,
        cwd=cwd,
        shell=shell,
        llm_provider=llm_provider,
        llm_status=llm_status,
        google=google,
        google_detail=google_detail,
        obsidian=obsidian,
        web_search=web_search,
        mcp_servers=mcp_status,
        tool_count=tool_count,
        db_size=db_size,
        obsidian_vault_path=str(config.obsidian_vault_path)
        if config.obsidian_vault_path
        else None,
    )


@dataclass
class SecurityCheckResult:
    """A single security posture check result."""

    severity: str  # "warn" | "error"
    check_id: str
    detail: str
    remediation: str


def check_security(
    _user_config_path: Path | None = None,
) -> list[SecurityCheckResult]:
    """Run security posture checks. Returns a list of findings (empty = all clear).

    Checks:
      1. User settings.json file permissions (warn if not 0o600)
      2. Exec-approvals wildcard entries (pattern == "*" is a catch-all security risk)
    """
    findings: list[SecurityCheckResult] = []

    user_cfg = _user_config_path or (USER_DIR / "settings.json")
    if Path(user_cfg).exists():
        # Check 1: user settings.json permissions
        mode = Path(user_cfg).stat().st_mode & 0o777
        if mode != 0o600:
            findings.append(
                SecurityCheckResult(
                    severity="warn",
                    check_id="user-config-permissions",
                    detail=f"~/.co-cli/settings.json permissions are {oct(mode)} (expected 0o600)",
                    remediation=f"chmod 600 {user_cfg}",
                )
            )

        # Check 2: wildcard shell_safe_commands entries
        try:
            data = json.loads(Path(user_cfg).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        shell_data = data.get("shell", {})
        safe_cmds = shell_data.get("safe_commands", []) if isinstance(shell_data, dict) else []
        if isinstance(safe_cmds, list) and "*" in safe_cmds:
            findings.append(
                SecurityCheckResult(
                    severity="warn",
                    check_id="user-config-shell-wildcard",
                    detail=(
                        f"shell.safe_commands contains '*' in {user_cfg} — "
                        "all shell commands are auto-approved without prompting"
                    ),
                    remediation=f"Remove '*' from shell.safe_commands in {user_cfg}",
                )
            )

    return findings


def render_security_findings(findings: list[SecurityCheckResult]) -> None:
    """Print security findings to the console. No output when findings list is empty."""
    if not findings:
        return
    for f in findings:
        console.print(f"[yellow]WARN[/yellow] [{f.check_id}] {f.detail}")
        console.print(f"  Remediation: {f.remediation}")


def render_status_table(info: StatusResult) -> Table:
    """Build a Rich Table from StatusResult using semantic styles."""
    table = Table(title=f"Co System Status (Provider: {info.llm_provider})")
    table.add_column("Component", style="accent")
    table.add_column("Status", style="info")
    table.add_column("Details", style="success")

    table.add_row("LLM", info.llm_status.title(), info.llm_provider)
    table.add_row("Shell", "Active", info.shell)
    table.add_row("Google", info.google.title(), info.google_detail)
    table.add_row("Obsidian", info.obsidian.title(), info.obsidian_vault_path or "None")
    table.add_row(
        "Web Search",
        info.web_search.title(),
        "Brave API" if info.web_search == "configured" else "—",
    )
    if info.mcp_servers:
        ready = sum(1 for _, s, _approval in info.mcp_servers if s == "ready")
        total = len(info.mcp_servers)
        status_str = f"{ready}/{total} ready" if ready < total else f"{total} ready"
        details = ", ".join(
            (
                f"{name} (ready, approval-gated)"
                if approval_req
                else f"{name} (ready, auto-approved)"
            )
            if s == "ready"
            else f"{name} ({s})"
            for name, s, approval_req in info.mcp_servers
        )
        table.add_row("MCP Servers", status_str, details)
    table.add_row("Database", "Active", info.db_size)
    if info.project_config:
        table.add_row("Project Config", "Active", info.project_config)

    return table
