"""Functional tests for configuration precedence and validation.

Tests exercise real load_config() — no mocks.
"""

import json
import pytest
from pydantic import ValidationError

from co_cli.config import find_project_config, load_config, Settings


def test_project_config_overrides_user(tmp_path):
    """Project .co-cli/settings.json overrides user settings for the same key."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({"theme": "light", "tool_retries": 5}))

    project_dir = tmp_path / "project"
    (project_dir / ".co-cli").mkdir(parents=True)
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({"theme": "dark"}))

    settings = load_config(_user_config_path=user_settings, _project_dir=project_dir)
    assert settings.theme == "dark"
    assert settings.tool_retries == 5


def test_env_overrides_project_config(tmp_path, monkeypatch):
    """Environment variables override project config."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({"theme": "dark"}))

    monkeypatch.setenv("CO_CLI_THEME", "light")

    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=project_dir,
    )
    assert settings.theme == "light"


def test_missing_project_config_uses_defaults(tmp_path):
    """No project config — load_config() uses user config + defaults."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({"theme": "dark"}))

    settings = load_config(_user_config_path=user_settings, _project_dir=tmp_path / "empty")
    assert settings.theme == "dark"


def test_malformed_project_config_skipped(tmp_path, capsys):
    """Malformed project settings.json is skipped gracefully."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text("not json{{{")

    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=project_dir,
    )
    assert settings.theme == "light"
    assert "Error loading project config" in capsys.readouterr().out


def test_web_policy_from_config(tmp_path):
    """web_policy object in project config is parsed correctly."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({
        "web_policy": {"search": "deny", "fetch": "ask"},
    }))

    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=project_dir,
    )
    assert settings.web_policy.search == "deny"
    assert settings.web_policy.fetch == "ask"


def test_project_config_partially_overrides_nested_web_policy(tmp_path):
    """Project config can override one nested web_policy field without redefining all fields."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({
        "web_policy": {"search": "deny", "fetch": "allow"},
    }))

    project_dir = tmp_path / "project"
    (project_dir / ".co-cli").mkdir(parents=True)
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({
        "web_policy": {"fetch": "ask"},
    }))

    settings = load_config(_user_config_path=user_settings, _project_dir=project_dir)
    assert settings.web_policy.search == "deny"
    assert settings.web_policy.fetch == "ask"


def test_project_config_partially_overrides_mcp_server(tmp_path):
    """Project config can override one MCP server field without redefining the whole server."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({
        "mcp_servers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "approval": "auto",
            }
        }
    }))

    project_dir = tmp_path / "project"
    (project_dir / ".co-cli").mkdir(parents=True)
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({
        "mcp_servers": {
            "github": {
                "approval": "never",
            }
        }
    }))

    settings = load_config(_user_config_path=user_settings, _project_dir=project_dir)
    github = settings.mcp_servers["github"]
    assert github.command == "npx"
    assert github.args == ["-y", "@modelcontextprotocol/server-github"]
    assert github.approval == "never"


def test_project_config_partially_overrides_role_models(tmp_path):
    """Project config can override one role_models entry without redefining all roles."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({
        "role_models": {
            "reasoning": ["base-reasoning"],
            "coding": ["base-coding"],
        }
    }))

    project_dir = tmp_path / "project"
    (project_dir / ".co-cli").mkdir(parents=True)
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({
        "role_models": {
            "coding": ["project-coding"],
        }
    }))

    settings = load_config(_user_config_path=user_settings, _project_dir=project_dir)
    assert [entry.model for entry in settings.role_models["reasoning"]] == ["base-reasoning"]
    assert [entry.model for entry in settings.role_models["coding"]] == ["project-coding"]


def test_env_overrides_web_policy(tmp_path, monkeypatch):
    """CO_CLI_WEB_POLICY_SEARCH/FETCH override file values."""
    monkeypatch.setenv("CO_CLI_WEB_POLICY_SEARCH", "ask")
    monkeypatch.setenv("CO_CLI_WEB_POLICY_FETCH", "deny")

    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=tmp_path / "empty",
    )
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


def test_gemini_api_key_overrides_env():
    """Regression: settings gemini_api_key must overwrite a pre-existing GEMINI_API_KEY env var."""
    import os
    from co_cli.agent import get_agent
    from co_cli.config import settings

    original_env = os.environ.get("GEMINI_API_KEY")
    original_key = settings.gemini_api_key
    original_provider = settings.llm_provider
    try:
        os.environ["GEMINI_API_KEY"] = "stale-key-from-env"
        settings.gemini_api_key = "settings-key-wins"
        settings.llm_provider = "gemini"
        get_agent()
        assert os.environ["GEMINI_API_KEY"] == "settings-key-wins"
    finally:
        settings.gemini_api_key = original_key
        settings.llm_provider = original_provider
        if original_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = original_env
