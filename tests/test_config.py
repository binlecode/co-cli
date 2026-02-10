"""Functional tests for project-level configuration.

Tests exercise real load_config() and find_project_config() — no mocks.
"""

import json
import pytest
from pydantic import ValidationError

from co_cli.config import find_project_config, load_config, Settings


def test_project_config_overrides_user(tmp_path, monkeypatch):
    """Project .co-cli/settings.json overrides user settings for the same key."""
    # User config: theme=light
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({"theme": "light", "tool_retries": 5}))

    # Project config: theme=dark (overrides user), tool_retries absent (keeps user value)
    project_dir = tmp_path / "project" / ".co-cli"
    project_dir.mkdir(parents=True)
    (project_dir / "settings.json").write_text(json.dumps({"theme": "dark"}))

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", user_settings)
    monkeypatch.chdir(tmp_path / "project")

    settings = load_config()
    assert settings.theme == "dark"       # project overrides user
    assert settings.tool_retries == 5     # user value preserved


def test_env_overrides_project_config(tmp_path, monkeypatch):
    """Environment variables override project config."""
    project_dir = tmp_path / ".co-cli"
    project_dir.mkdir()
    (project_dir / "settings.json").write_text(json.dumps({"theme": "dark"}))

    # No user config
    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CO_CLI_THEME", "light")

    settings = load_config()
    assert settings.theme == "light"  # env wins over project


def test_missing_project_config_is_noop(tmp_path, monkeypatch):
    """No .co-cli/settings.json in cwd — load_config() uses user config + defaults."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({"theme": "dark"}))

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", user_settings)
    monkeypatch.chdir(tmp_path)  # no .co-cli/ here

    settings = load_config()
    assert settings.theme == "dark"  # user value, no project override


def test_find_project_config_returns_path(tmp_path, monkeypatch):
    """find_project_config() returns the path when .co-cli/settings.json exists."""
    project_dir = tmp_path / ".co-cli"
    project_dir.mkdir()
    config_file = project_dir / "settings.json"
    config_file.write_text(json.dumps({"theme": "dark"}))

    monkeypatch.chdir(tmp_path)
    assert find_project_config() == config_file


def test_find_project_config_returns_none(tmp_path, monkeypatch):
    """find_project_config() returns None when no .co-cli/settings.json in cwd."""
    monkeypatch.chdir(tmp_path)
    assert find_project_config() is None


def test_malformed_project_config_skipped(tmp_path, monkeypatch, capsys):
    """Malformed project settings.json is skipped with a warning."""
    project_dir = tmp_path / ".co-cli"
    project_dir.mkdir()
    (project_dir / "settings.json").write_text("not json{{{")

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)

    settings = load_config()  # should not raise
    assert settings.theme == "light"  # falls back to default

    captured = capsys.readouterr()
    assert "Error loading project config" in captured.out


def test_web_policy_parses_from_project_config(tmp_path, monkeypatch):
    """web_policy object in project config is parsed into Settings."""
    project_dir = tmp_path / ".co-cli"
    project_dir.mkdir()
    (project_dir / "settings.json").write_text(json.dumps({
        "web_policy": {"search": "deny", "fetch": "ask"},
    }))

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)

    settings = load_config()
    assert settings.web_policy.search == "deny"
    assert settings.web_policy.fetch == "ask"


def test_env_overrides_web_policy_fields(tmp_path, monkeypatch):
    """CO_CLI_WEB_POLICY_SEARCH/FETCH override file values."""
    project_dir = tmp_path / ".co-cli"
    project_dir.mkdir()
    (project_dir / "settings.json").write_text(json.dumps({
        "web_policy": {"search": "allow", "fetch": "allow"},
    }))

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CO_CLI_WEB_POLICY_SEARCH", "ask")
    monkeypatch.setenv("CO_CLI_WEB_POLICY_FETCH", "deny")

    settings = load_config()
    assert settings.web_policy.search == "ask"
    assert settings.web_policy.fetch == "deny"


def test_personality_validation():
    """Personality field validates allowed values."""
    # Valid personalities
    settings = Settings(personality="finch")
    assert settings.personality == "finch"

    settings = Settings(personality="jeff")
    assert settings.personality == "jeff"

    settings = Settings(personality="friendly")
    assert settings.personality == "friendly"

    settings = Settings(personality="terse")
    assert settings.personality == "terse"

    settings = Settings(personality="inquisitive")
    assert settings.personality == "inquisitive"

    # Invalid personality
    with pytest.raises(ValidationError, match="personality must be one of"):
        Settings(personality="invalid")


def test_personality_default():
    """Personality defaults to 'finch'."""
    settings = Settings()
    assert settings.personality == "finch"


def test_personality_from_project_config(tmp_path, monkeypatch):
    """Personality can be set in project config."""
    project_dir = tmp_path / ".co-cli"
    project_dir.mkdir()
    (project_dir / "settings.json").write_text(json.dumps({"personality": "friendly"}))

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)

    settings = load_config()
    assert settings.personality == "friendly"


def test_personality_from_env(tmp_path, monkeypatch):
    """Personality can be set via CO_CLI_PERSONALITY env var."""
    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CO_CLI_PERSONALITY", "terse")

    settings = load_config()
    assert settings.personality == "terse"
