"""Security posture checks for co-cli session startup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from co_cli.config.core import USER_DIR
from co_cli.display.core import console


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
      2. shell.safe_commands wildcard entries (pattern == "*" auto-approves all shell commands)
      3. .env file permissions (warn if not 0o600)
    """
    findings: list[SecurityCheckResult] = []

    user_cfg = Path(_user_config_path or (USER_DIR / "settings.json"))
    if user_cfg.exists():
        mode = user_cfg.stat().st_mode & 0o777
        if mode != 0o600:
            findings.append(
                SecurityCheckResult(
                    severity="warn",
                    check_id="user-config-permissions",
                    detail=f"{user_cfg} permissions are {oct(mode)} (expected 0o600)",
                    remediation=f"chmod 600 {user_cfg}",
                )
            )

        try:
            data = json.loads(user_cfg.read_text(encoding="utf-8"))
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

    dot_env = user_cfg.parent / ".env"
    if dot_env.exists():
        mode = dot_env.stat().st_mode & 0o777
        if mode != 0o600:
            findings.append(
                SecurityCheckResult(
                    severity="warn",
                    check_id="dot-env-permissions",
                    detail=f"{dot_env} permissions are {oct(mode)} (expected 0o600)",
                    remediation=f"chmod 600 {dot_env}",
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
