"""Functional tests for configuration precedence and validation.

Tests exercise real load_config() — no mocks.
"""

import json
import pytest
from pydantic import ValidationError

from co_cli.config import find_project_config, load_config, Settings


def test_project_config_overrides_user(tmp_path, monkeypatch):
    """Project .co-cli/settings.json overrides user settings for the same key."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({"theme": "light", "tool_retries": 5}))

    project_dir = tmp_path / "project" / ".co-cli"
    project_dir.mkdir(parents=True)
    (project_dir / "settings.json").write_text(json.dumps({"theme": "dark"}))

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", user_settings)
    monkeypatch.chdir(tmp_path / "project")

    settings = load_config()
    assert settings.theme == "dark"
    assert settings.tool_retries == 5


def test_env_overrides_project_config(tmp_path, monkeypatch):
    """Environment variables override project config."""
    project_dir = tmp_path / ".co-cli"
    project_dir.mkdir()
    (project_dir / "settings.json").write_text(json.dumps({"theme": "dark"}))

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CO_CLI_THEME", "light")

    settings = load_config()
    assert settings.theme == "light"


def test_missing_project_config_uses_defaults(tmp_path, monkeypatch):
    """No project config — load_config() uses user config + defaults."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({"theme": "dark"}))

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", user_settings)
    monkeypatch.chdir(tmp_path)

    settings = load_config()
    assert settings.theme == "dark"


def test_malformed_project_config_skipped(tmp_path, monkeypatch, capsys):
    """Malformed project settings.json is skipped gracefully."""
    project_dir = tmp_path / ".co-cli"
    project_dir.mkdir()
    (project_dir / "settings.json").write_text("not json{{{")

    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)

    settings = load_config()
    assert settings.theme == "light"
    assert "Error loading project config" in capsys.readouterr().out


def test_web_policy_from_config(tmp_path, monkeypatch):
    """web_policy object in project config is parsed correctly."""
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


def test_env_overrides_web_policy(tmp_path, monkeypatch):
    """CO_CLI_WEB_POLICY_SEARCH/FETCH override file values."""
    monkeypatch.setattr("co_cli.config.SETTINGS_FILE", tmp_path / "nonexistent.json")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CO_CLI_WEB_POLICY_SEARCH", "ask")
    monkeypatch.setenv("CO_CLI_WEB_POLICY_FETCH", "deny")

    settings = load_config()
    assert settings.web_policy.search == "ask"
    assert settings.web_policy.fetch == "deny"


def test_web_http_retry_bounds_validation():
    """Backoff base must not exceed backoff max."""
    with pytest.raises(ValidationError, match="web_http_backoff_base_seconds"):
        Settings(
            web_http_backoff_base_seconds=10.0,
            web_http_backoff_max_seconds=1.0,
        )


def test_personality_validation():
    """Valid personality passes; invalid raises ValidationError."""
    assert Settings(personality="finch").personality == "finch"
    with pytest.raises(ValidationError, match="personality must be one of"):
        Settings(personality="invalid")


def test_max_request_limit_default():
    """Turn limit default is 50 (§5.1: increased to accommodate sub-agent delegations)."""
    s = Settings()
    assert s.max_request_limit == 50
