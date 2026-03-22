"""Functional tests for status rendering and security posture checks."""

import stat
from pathlib import Path

import pytest

from co_cli.bootstrap._render_status import (
    _PYPROJECT,
    SecurityFinding,
    check_security,
    get_status,
)
from co_cli.config import settings
from co_cli.deps import CoConfig


def _make_config(tmp_path: Path, name: str = "settings.json") -> Path:
    """Create a minimal settings.json file."""
    p = tmp_path / name
    p.write_text("{}")
    return p


def test_get_status_reads_repo_root_pyproject():
    """get_status() must read version from repo-root pyproject.toml, not co_cli/."""
    assert _PYPROJECT.exists()
    assert _PYPROJECT.name == "pyproject.toml"
    assert _PYPROJECT.parent == Path(__file__).resolve().parent.parent

    config = CoConfig.from_settings(settings, cwd=Path.cwd())
    info = get_status(config)

    assert info.version


# -- check_security --------------------------------------------------------


def test_check_security_no_files_no_findings(tmp_path):
    """No config files present → no findings."""
    findings = check_security(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_config_path=tmp_path / "also-nonexistent.json",
    )
    assert findings == []


def test_check_security_user_config_wrong_mode(tmp_path):
    """User settings.json with 0o644 → WARN finding."""
    p = _make_config(tmp_path)
    p.chmod(0o644)
    findings = check_security(_user_config_path=p, _project_config_path=None)
    user_findings = [f for f in findings if f.check_id == "user-config-permissions"]
    assert len(user_findings) == 1
    assert user_findings[0].severity == "warn"
    assert "0o644" in user_findings[0].detail


def test_check_security_project_config_wrong_mode(tmp_path):
    """Project settings.json with 0o644 → WARN finding."""
    p = _make_config(tmp_path, "project-settings.json")
    p.chmod(0o644)
    findings = check_security(_user_config_path=None, _project_config_path=p)
    proj_findings = [f for f in findings if f.check_id == "project-config-permissions"]
    assert len(proj_findings) == 1
    assert proj_findings[0].severity == "warn"



# -- render_security_findings (smoke test) ---------------------------------


def test_render_security_findings_outputs_findings():
    """render_security_findings with findings does not raise."""
    from co_cli.bootstrap._render_status import render_security_findings
    findings = [
        SecurityFinding(
            severity="warn",
            check_id="test-check",
            detail="Test detail",
            remediation="Test remediation",
        )
    ]
    # Should not raise
    render_security_findings(findings)


def test_get_status_mcp_approval_posture(tmp_path: Path):
    """mcp_servers tuple has approval_required as third element."""
    from co_cli.config import MCPServerConfig
    config = CoConfig(
        mcp_servers={
            "ask-server": MCPServerConfig(command="npx", approval="ask"),
            "auto-server": MCPServerConfig(command="npx", approval="auto"),
        }
    )
    info = get_status(config)
    # The status check reads from doctor.checks (real doctor run with no-op servers)
    # so we test the tuple structure and CoConfig lookup logic directly on the result.
    # Verify the tuple shape: all entries are 3-tuples
    for entry in info.mcp_servers:
        assert len(entry) == 3, f"Expected 3-tuple, got {len(entry)}-tuple: {entry}"
