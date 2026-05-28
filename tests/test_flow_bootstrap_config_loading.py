"""Behavioral tests for config loading at startup — dotenv, env precedence, security, skills.

Covers: load_config() (dotenv merging, env precedence, knowledge sub-settings),
check_security() (.env permissions), and load_skills() (project skill registration).
"""

from pathlib import Path

from co_cli.bootstrap.security import check_security
from co_cli.config.core import load_config


def test_load_config_dotenv_applied(tmp_path: Path) -> None:
    """Vars in .env are applied to config when no shell env vars are present."""
    (tmp_path / ".env").write_text("CO_THEME=dark\n", encoding="utf-8")

    # _env={} isolates from real shell so only the .env values are in scope
    result = load_config(_user_config_path=tmp_path / "settings.json", _env={})

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
    """When no .env is present, load_config() returns defaults unchanged."""
    # _env={} isolates from real shell so the assertion is not contaminated by CO_THEME in env
    result = load_config(_user_config_path=tmp_path / "settings.json", _env={})
    assert result.theme == "light"


def test_load_config_dotenv_empty_value_uses_default(tmp_path: Path) -> None:
    """Empty-value .env entries must not override Settings defaults."""
    (tmp_path / ".env").write_text("CO_THEME=\n", encoding="utf-8")

    result = load_config(_user_config_path=tmp_path / "settings.json", _env={})

    assert result.theme == "light"


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


def test_memory_settings_env_prefix_overrides_default(tmp_path: Path) -> None:
    """CO_MEMORY_CHUNK_TOKENS env var overrides the MemorySettings default."""
    result = load_config(
        _user_config_path=tmp_path / "settings.json",
        _env={"CO_MEMORY_CHUNK_TOKENS": "42"},
    )

    assert result.memory.chunk_tokens == 42


def test_memory_settings_env_overrides_json_config(tmp_path: Path) -> None:
    """Env var takes priority over the JSON config value for memory fields."""
    (tmp_path / "settings.json").write_text('{"memory": {"chunk_tokens": 200}}', encoding="utf-8")

    result = load_config(
        _user_config_path=tmp_path / "settings.json",
        _env={"CO_MEMORY_CHUNK_TOKENS": "99"},
    )

    assert result.memory.chunk_tokens == 99


def test_memory_settings_json_config_applies_without_env(tmp_path: Path) -> None:
    """JSON config value is used for memory fields when no env var is set."""
    (tmp_path / "settings.json").write_text('{"memory": {"chunk_tokens": 300}}', encoding="utf-8")

    result = load_config(_user_config_path=tmp_path / "settings.json", _env={})

    assert result.memory.chunk_tokens == 300


def test_repl_settings_defaults(tmp_path: Path) -> None:
    """ReplSettings defaults preserve Phase-1 behavior: unbounded, oldest-drop."""
    result = load_config(_user_config_path=tmp_path / "settings.json", _env={})

    assert result.repl.queue_cap == 0
    assert result.repl.drop_policy == "oldest"


def test_repl_settings_env_overrides_defaults(tmp_path: Path) -> None:
    """CO_REPL_QUEUE_CAP / CO_REPL_DROP_POLICY env vars override the defaults via fill_from_env."""
    result = load_config(
        _user_config_path=tmp_path / "settings.json",
        _env={"CO_REPL_QUEUE_CAP": "5", "CO_REPL_DROP_POLICY": "newest"},
    )

    assert result.repl.queue_cap == 5
    assert result.repl.drop_policy == "newest"


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

    skill_index = load_skills(skills_dir)

    assert "test-bootstrap-skill" in skill_index, (
        "Project skill must appear in skill_index after load_skills"
    )
