"""Environment / health checks and status table rendering."""

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.table import Table

from co_cli.config import settings, DATA_DIR, project_config_path, CONFIG_DIR


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@dataclass
class StatusInfo:
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
    mcp_servers: list[tuple[str, str]]  # [(name, "configured"), ...]
    tool_count: int
    db_size: str  # "1.2 KB" | "0 KB"
    project_config: str | None  # path to .co-cli/settings.json or None


def get_status(tool_count: int = 0) -> StatusInfo:
    """Gather system status into a plain dataclass (no display side-effects)."""

    # -- version --
    version = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]

    # -- git branch --
    try:
        git_branch: str | None = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        git_branch = None

    # -- cwd --
    cwd = Path.cwd().name

    # -- shell --
    shell = "subprocess (approval-gated)"

    # -- llm --
    provider = settings.llm_provider.lower()
    reasoning_chain = settings.model_roles.get("reasoning", [])
    if not reasoning_chain:
        llm_provider = f"{provider.title()} (no reasoning model configured)"
        llm_status = "misconfigured"
    elif provider == "gemini":
        active_model = reasoning_chain[0]
        llm_provider = f"Gemini ({active_model})"
        llm_status = "configured" if settings.gemini_api_key else "missing key"
    else:
        active_model = reasoning_chain[0]
        llm_provider = f"Ollama ({active_model})"
        try:
            import httpx

            resp = httpx.get(settings.ollama_host)
            llm_status = "online" if resp.status_code == 200 else "offline"
        except Exception:
            llm_status = "offline"

    # -- google credentials --
    from co_cli.tools._google_auth import GOOGLE_TOKEN_PATH, ADC_PATH

    if (
        settings.google_credentials_path
        and os.path.exists(os.path.expanduser(settings.google_credentials_path))
    ):
        google = "configured"
        google_detail = settings.google_credentials_path
    elif GOOGLE_TOKEN_PATH.exists():
        google = "configured"
        google_detail = str(GOOGLE_TOKEN_PATH)
    elif ADC_PATH.exists():
        google = "adc"
        google_detail = str(ADC_PATH)
    else:
        google = "not found"
        google_detail = "Run 'co chat' to auto-setup or install gcloud"

    # -- obsidian --
    obsidian_path = settings.obsidian_vault_path
    obsidian = (
        "configured"
        if obsidian_path and os.path.exists(obsidian_path)
        else "not found"
    )

    # -- web search --
    web_search = "configured" if settings.brave_search_api_key else "not configured"

    # -- mcp servers --
    mcp_status = []
    for name, cfg in settings.mcp_servers.items():
        if cfg.url:
            mcp_status.append((name, "remote (url)"))
        elif shutil.which(cfg.command):
            mcp_status.append((name, "ready"))
        else:
            mcp_status.append((name, f"{cfg.command} not found"))

    # -- db size --
    db_path = DATA_DIR / "co-cli.db"
    db_size = f"{os.path.getsize(db_path) / 1024:.1f} KB" if db_path.exists() else "0 KB"

    return StatusInfo(
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
        project_config=str(project_config_path) if project_config_path else None,
    )


@dataclass
class SecurityFinding:
    """A single security posture check result."""

    severity: str  # "warn" | "error"
    check_id: str
    detail: str
    remediation: str


def check_security(
    _user_config_path: Optional[Path] = None,
    _project_config_path: Optional[Path] = None,
    _approvals_path: Optional[Path] = None,
) -> list[SecurityFinding]:
    """Run security posture checks. Returns a list of findings (empty = all clear).

    Checks:
      1. User settings.json file permissions (warn if not 0o600)
      2. Project settings.json file permissions (warn if not 0o600)
      3. Exec-approvals wildcard entries (pattern == "*" is a catch-all security risk)
    """
    findings: list[SecurityFinding] = []

    # Check 1: user settings.json permissions
    user_cfg = _user_config_path or (CONFIG_DIR / "settings.json")
    if Path(user_cfg).exists():
        mode = Path(user_cfg).stat().st_mode & 0o777
        if mode != 0o600:
            findings.append(SecurityFinding(
                severity="warn",
                check_id="user-config-permissions",
                detail=f"~/.config/co-cli/settings.json permissions are {oct(mode)} (expected 0o600)",
                remediation=f"chmod 600 {user_cfg}",
            ))

    # Check 2: project settings.json permissions
    project_cfg = _project_config_path or project_config_path
    if project_cfg and Path(project_cfg).exists():
        mode = Path(project_cfg).stat().st_mode & 0o777
        if mode != 0o600:
            findings.append(SecurityFinding(
                severity="warn",
                check_id="project-config-permissions",
                detail=f".co-cli/settings.json permissions are {oct(mode)} (expected 0o600)",
                remediation=f"chmod 600 {project_cfg}",
            ))

    # Check 3: exec-approvals wildcard catch-all entries
    approvals_path = _approvals_path or (Path.cwd() / ".co-cli" / "exec-approvals.json")
    if approvals_path.exists():
        from co_cli._exec_approvals import load_approvals
        entries = load_approvals(approvals_path)
        wildcards = [e for e in entries if e.get("pattern") == "*"]
        if wildcards:
            findings.append(SecurityFinding(
                severity="warn",
                check_id="exec-approval-wildcard",
                detail=f"{len(wildcards)} exec approval(s) with catch-all pattern '*' (approves any shell command)",
                remediation="Run /approvals clear to review and remove wildcard entries",
            ))

    return findings


def render_security_findings(findings: list[SecurityFinding]) -> None:
    """Print security findings to the console. No output when findings list is empty."""
    if not findings:
        return
    from co_cli.display import console
    for f in findings:
        console.print(f"[yellow]WARN[/yellow] [{f.check_id}] {f.detail}")
        console.print(f"  Remediation: {f.remediation}")


def render_status_table(info: StatusInfo) -> Table:
    """Build a Rich Table from StatusInfo using semantic styles."""
    table = Table(title=f"Co System Status (Provider: {info.llm_provider})")
    table.add_column("Component", style="accent")
    table.add_column("Status", style="info")
    table.add_column("Details", style="success")

    table.add_row("LLM", info.llm_status.title(), info.llm_provider)
    table.add_row("Shell", "Active", info.shell)
    table.add_row("Google", info.google.title(), info.google_detail)
    table.add_row("Obsidian", info.obsidian.title(), settings.obsidian_vault_path or "None")
    table.add_row("Web Search", info.web_search.title(), "Brave API" if info.web_search == "configured" else "—")
    if info.mcp_servers:
        ready = sum(1 for _, s in info.mcp_servers if s == "ready")
        total = len(info.mcp_servers)
        status_str = f"{ready}/{total} ready" if ready < total else f"{total} ready"
        details = ", ".join(
            name if s == "ready" else f"{name} ({s})"
            for name, s in info.mcp_servers
        )
        table.add_row("MCP Servers", status_str, details)
    table.add_row("Database", "Active", info.db_size)
    if info.project_config:
        table.add_row("Project Config", "Active", info.project_config)

    return table
