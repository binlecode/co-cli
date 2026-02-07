"""Pure-data environment / health checks — no Rich, no console."""

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from co_cli.config import settings, DATA_DIR, project_config_path


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@dataclass
class StatusInfo:
    version: str
    git_branch: str | None
    cwd: str  # basename
    docker: str  # "ready" | "unavailable"
    llm_provider: str  # "Gemini (model)" | "Ollama (model)"
    llm_status: str  # "configured" | "online" | "offline" | "missing key"
    google: str  # "configured" | "adc" | "not found"
    google_detail: str
    obsidian: str  # "configured" | "not found"
    slack: str  # "configured" | "not configured"
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

    # -- docker --
    try:
        subprocess.check_output(["docker", "info"], stderr=subprocess.DEVNULL)
        docker = "ready"
    except Exception:
        docker = "unavailable"

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
    from co_cli.google_auth import GOOGLE_TOKEN_PATH, ADC_PATH

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

    # -- db size --
    db_path = DATA_DIR / "co-cli.db"
    db_size = f"{os.path.getsize(db_path) / 1024:.1f} KB" if db_path.exists() else "0 KB"

    return StatusInfo(
        version=version,
        git_branch=git_branch,
        cwd=cwd,
        docker=docker,
        llm_provider=llm_provider,
        llm_status=llm_status,
        google=google,
        google_detail=google_detail,
        obsidian=obsidian,
        slack=slack,
        tool_count=tool_count,
        db_size=db_size,
        project_config=str(project_config_path) if project_config_path else None,
    )
