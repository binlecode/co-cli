"""Consolidated E2E tests for test_flow_bootstrap_session."""

from pathlib import Path

from tests._settings import make_settings

from co_cli.bootstrap.core import restore_session
from co_cli.bootstrap.security import check_security
from co_cli.config.core import load_config, settings
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.display.core import TerminalFrontend
from co_cli.memory.session import session_filename
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(
    tmp_path: Path,
    *,
    mcp_servers: dict | None = None,
) -> CoDeps:
    config = make_settings(
        mcp_servers=mcp_servers if mcp_servers is not None else {},
    )
    runtime = CoRuntimeState()
    return CoDeps(
        shell=ShellBackend(),
        knowledge_store=None,
        config=config,
        session=CoSessionState(),
        runtime=runtime,
        sessions_dir=tmp_path / "sessions",
        knowledge_dir=tmp_path / "knowledge",
    )


def test_restore_session_picks_most_recent(tmp_path: Path) -> None:
    """restore_session() must pick the most recent session by lexicographic filename sort."""
    from datetime import UTC, datetime

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    older = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 4, 11, 12, 0, 0, tzinfo=UTC)
    old_path = sessions_dir / session_filename(older, "aaaaaaaa-0000-0000-0000-000000000000")
    new_path = sessions_dir / session_filename(newer, "bbbbbbbb-0000-0000-0000-000000000000")
    old_path.touch()
    new_path.touch()

    deps = _make_deps(tmp_path)
    result = restore_session(deps, TerminalFrontend())

    assert result == new_path, "restore_session() must pick the most recently dated session"


def test_load_config_dotenv_applied(tmp_path: Path) -> None:
    """Vars in .env are applied to config when no explicit _env override is given."""
    (tmp_path / ".env").write_text("CO_THEME=dark\n", encoding="utf-8")

    result = load_config(_user_config_path=tmp_path / "settings.json")

    assert result.theme == "dark"


def test_load_config_env_wins_over_dotenv(tmp_path: Path) -> None:
    """Explicit _env takes precedence over .env vars — shell env wins."""
    (tmp_path / ".env").write_text("CO_THEME=dark\n", encoding="utf-8")

    result = load_config(
        _user_config_path=tmp_path / "settings.json",
        _env={"CO_THEME": "light"},
    )

    assert result.theme == "light"


def test_load_config_no_dot_env_uses_defaults(tmp_path: Path) -> None:
    """When no .env is present, load_config() falls back to defaults unchanged."""
    settings_default = load_config(_user_config_path=tmp_path / "settings.json")
    assert settings_default.theme == "light"


def test_check_security_dot_env_wrong_mode(tmp_path: Path) -> None:
    """.env with 0o644 → WARN finding with dot-env-permissions check_id."""
    dot_env = tmp_path / ".env"
    dot_env.write_text("CO_THEME=dark\n", encoding="utf-8")
    dot_env.chmod(0o644)

    findings = check_security(_user_config_path=tmp_path / "settings.json")
    env_findings = [f for f in findings if f.check_id == "dot-env-permissions"]
    assert len(env_findings) == 1
    assert env_findings[0].severity == "warn"
    assert "0o644" in env_findings[0].detail


def test_check_security_dot_env_correct_mode_no_finding(tmp_path: Path) -> None:
    """.env with 0o600 → no dot-env-permissions finding."""
    dot_env = tmp_path / ".env"
    dot_env.write_text("CO_THEME=dark\n", encoding="utf-8")
    dot_env.chmod(0o600)

    findings = check_security(_user_config_path=tmp_path / "settings.json")
    assert not any(f.check_id == "dot-env-permissions" for f in findings)


def test_check_security_no_dot_env_no_finding(tmp_path: Path) -> None:
    """No .env present → no dot-env-permissions finding."""
    findings = check_security(_user_config_path=tmp_path / "settings.json")
    assert not any(f.check_id == "dot-env-permissions" for f in findings)


def test_skill_loading_project_skill_registered(tmp_path: Path) -> None:
    """Project skill directory with one valid skill: skill appears in loaded commands."""
    from co_cli.skills.loader import load_skills

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_content = (
        "---\n"
        "description: Test skill for bootstrap functional tests\n"
        "---\n\n"
        "Perform a test action.\n"
    )
    (skills_dir / "test-bootstrap-skill.md").write_text(skill_content, encoding="utf-8")

    skill_commands = load_skills(skills_dir, settings=settings)

    assert "test-bootstrap-skill" in skill_commands, (
        "Project skill must appear in skill_commands after load_skills"
    )
