"""Functional tests for status rendering and security posture checks."""

import json
from pathlib import Path

from co_cli.bootstrap.render_status import (
    check_security,
    get_status,
)
from co_cli.config._core import settings


def _make_config(tmp_path: Path, name: str = "settings.json") -> Path:
    """Create a minimal settings.json file."""
    p = tmp_path / name
    p.write_text("{}")
    return p


def test_get_status_reads_repo_root_pyproject():
    """get_status() reads version from repo-root pyproject.toml in MAJOR.MINOR.PATCH format."""
    import re

    info = get_status(settings)

    assert re.fullmatch(r"\d+\.\d+\.\d+", info.version), (
        f"Version must match MAJOR.MINOR.PATCH, got {info.version!r}"
    )


# -- check_security --------------------------------------------------------


def test_check_security_no_files_no_findings(tmp_path):
    """No config files present → no findings."""
    findings = check_security(_user_config_path=tmp_path / "nonexistent.json")
    assert findings == []


def test_check_security_user_config_wrong_mode(tmp_path):
    """User settings.json with 0o644 → WARN finding."""
    p = _make_config(tmp_path)
    p.chmod(0o644)
    findings = check_security(_user_config_path=p)
    user_findings = [f for f in findings if f.check_id == "user-config-permissions"]
    assert len(user_findings) == 1
    assert user_findings[0].severity == "warn"
    assert "0o644" in user_findings[0].detail


def test_check_security_user_config_shell_wildcard(tmp_path):
    """User settings.json with shell.safe_commands containing '*' → WARN wildcard finding."""
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"shell": {"safe_commands": ["ls", "*", "cat"]}}), encoding="utf-8")
    findings = check_security(_user_config_path=p)
    wildcard_findings = [f for f in findings if f.check_id == "user-config-shell-wildcard"]
    assert len(wildcard_findings) == 1
    assert wildcard_findings[0].severity == "warn"
    assert "*" in wildcard_findings[0].detail


def test_check_security_no_wildcard_when_safe_commands_normal(tmp_path):
    """shell.safe_commands without '*' → no wildcard finding."""
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps({"shell": {"safe_commands": ["ls", "cat", "git status"]}}), encoding="utf-8"
    )
    findings = check_security(_user_config_path=p)
    wildcard_findings = [f for f in findings if "wildcard" in f.check_id]
    assert wildcard_findings == []
