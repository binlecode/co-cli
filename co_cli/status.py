"""Environment / health checks and status table rendering."""

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from rich.table import Table

from co_cli.config import settings, DATA_DIR, project_config_path


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@dataclass
class StatusInfo:
    version: str
    git_branch: str | None
    cwd: str  # basename
    sandbox: str  # "Docker (full isolation)" | "subprocess (no isolation)" | "unavailable"
    llm_provider: str  # "Gemini (model)" | "Ollama (model)"
    llm_status: str  # "configured" | "online" | "offline" | "missing key"
    google: str  # "configured" | "adc" | "not found"
    google_detail: str
    obsidian: str  # "configured" | "not found"
    slack: str  # "configured" | "not configured"
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

    # -- sandbox --
    backend = settings.sandbox_backend
    if backend == "subprocess":
        sandbox = "subprocess (no isolation)"
    elif backend in ("docker", "auto"):
        try:
            subprocess.check_output(["docker", "info"], stderr=subprocess.DEVNULL)
            sandbox = "Docker (full isolation)"
        except Exception:
            if backend == "docker":
                sandbox = "unavailable"
            else:
                sandbox = "subprocess (no isolation)"
    else:
        sandbox = "unavailable"

    # -- llm --
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        llm_provider = f"Gemini ({settings.gemini_model})"
        llm_status = "configured" if settings.gemini_api_key else "missing key"
    else:
        llm_provider = f"Ollama ({settings.ollama_model})"
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

    # -- slack --
    slack = "configured" if settings.slack_bot_token else "not configured"

    # -- web search --
    web_search = "configured" if settings.brave_search_api_key else "not configured"

    # -- mcp servers --
    mcp_status = []
    for name, cfg in settings.mcp_servers.items():
        if shutil.which(cfg.command):
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
        sandbox=sandbox,
        llm_provider=llm_provider,
        llm_status=llm_status,
        google=google,
        google_detail=google_detail,
        obsidian=obsidian,
        slack=slack,
        web_search=web_search,
        mcp_servers=mcp_status,
        tool_count=tool_count,
        db_size=db_size,
        project_config=str(project_config_path) if project_config_path else None,
    )


def render_status_table(info: StatusInfo) -> Table:
    """Build a Rich Table from StatusInfo using semantic styles."""
    table = Table(title=f"Co System Status (Provider: {info.llm_provider})")
    table.add_column("Component", style="accent")
    table.add_column("Status", style="info")
    table.add_column("Details", style="success")

    table.add_row("LLM", info.llm_status.title(), info.llm_provider)
    sandbox_status = "Active" if "unavailable" not in info.sandbox else "Unavailable"
    table.add_row("Sandbox", sandbox_status, info.sandbox)
    table.add_row("Google", info.google.title(), info.google_detail)
    table.add_row("Obsidian", info.obsidian.title(), settings.obsidian_vault_path or "None")
    table.add_row("Slack", info.slack.title(), "Bot token" if info.slack == "configured" else "—")
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
