"""Welcome banner display for the Co CLI chat startup sequence."""

import subprocess
import tomllib
from pathlib import Path

from co_cli.bootstrap._check import RuntimeCheck
from co_cli.config import ROLE_REASONING
from co_cli.deps import CoConfig
from co_cli.display import console


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

_ASCII_ART = {
    "dark": [
        "    █▀▀ █▀█   █▀▀ █   █",
        "    █▄▄ █▄█   █▄▄ █▄▄ █",
    ],
    "light": [
        "    ┌─┐ ┌─┐   ┌─┐ ┬   ┬",
        "    │   │ │   │   │   │",
        "    └─┘ └─┘   └─┘ └─┘ ┴",
    ],
}


def display_welcome_banner(runtime_check: RuntimeCheck, config: CoConfig) -> None:
    """Render welcome banner with ASCII art, model, and environment info."""
    from rich.panel import Panel

    art = "\n".join(_ASCII_ART.get(config.theme, _ASCII_ART["light"]))

    version = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]

    reasoning_entry = config.role_models.get(ROLE_REASONING)
    if reasoning_entry:
        llm_provider = f"{config.llm_provider} / {reasoning_entry.model}"
    else:
        llm_provider = config.llm_provider

    tool_count = runtime_check.status.get("tool_count", 0)

    try:
        git_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        git_branch = ""

    if runtime_check.findings:
        verdict = f"[error]✗ {len(runtime_check.findings)} issue(s) — run /status[/error]"
    elif runtime_check.fallbacks:
        verdict = f"[dim]· degraded ({len(runtime_check.fallbacks)} fallback(s))[/dim]"
    else:
        verdict = "[success]✓ All systems operational[/success]"

    lines = [
        f"\n[accent]{art}[/accent]\n",
        f"    v{version} — CLI Assistant",
        f"    Model: [accent]{llm_provider}[/accent]",
        f"    Tools: {tool_count}  Shell: subprocess (approval-gated)",
        f"    Dir: {Path.cwd().name}" + (f"  ({git_branch})" if git_branch else ""),
        "",
        f"    {verdict}",
        f"    [dim]Type /help for commands, 'exit' to quit[/dim]",
    ]
    console.print(Panel("\n".join(lines), border_style="accent", expand=False))
