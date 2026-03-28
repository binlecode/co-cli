"""Functional tests for configuration precedence and validation.

Tests exercise real load_config() — no mocks.
"""

import json
import os
from pathlib import Path
import pytest
from pydantic import ValidationError

import co_cli.config as config_module
from co_cli.config import find_project_config, load_config, Settings, ROLE_REASONING


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


def test_env_overrides_project_config(tmp_path):
    """Environment variables override project config."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({"theme": "dark"}))

    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=project_dir,
        _env={"CO_CLI_THEME": "light"},
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
                "approval": "ask",
            }
        }
    }))

    project_dir = tmp_path / "project"
    (project_dir / ".co-cli").mkdir(parents=True)
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({
        "mcp_servers": {
            "github": {
                "approval": "auto",
            }
        }
    }))

    settings = load_config(_user_config_path=user_settings, _project_dir=project_dir)
    github = settings.mcp_servers["github"]
    assert github.command == "npx"
    assert github.args == ["-y", "@modelcontextprotocol/server-github"]
    assert github.approval == "auto"


def test_project_config_partially_overrides_role_models(tmp_path):
    """Project config can override one role_models entry without redefining all roles."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(json.dumps({
        "role_models": {
            ROLE_REASONING: "base-reasoning",
            "coding": "base-coding",
        }
    }))

    project_dir = tmp_path / "project"
    (project_dir / ".co-cli").mkdir(parents=True)
    (project_dir / ".co-cli" / "settings.json").write_text(json.dumps({
        "role_models": {
            "coding": "project-coding",
        }
    }))

    settings = load_config(_user_config_path=user_settings, _project_dir=project_dir)
    assert settings.role_models[ROLE_REASONING].model == "base-reasoning"
    assert settings.role_models["coding"].model == "project-coding"


def test_env_overrides_web_policy(tmp_path):
    """CO_CLI_WEB_POLICY_SEARCH/FETCH override file values."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=tmp_path / "empty",
        _env={"CO_CLI_WEB_POLICY_SEARCH": "ask", "CO_CLI_WEB_POLICY_FETCH": "deny"},
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


def test_default_provider_is_ollama_openai(tmp_path):
    """When no llm_provider is set, the default must be 'ollama-openai' (P1 rename)."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=tmp_path / "empty",
    )
    assert settings.llm_provider == "ollama-openai"


def test_ollama_native_provider_rejected(tmp_path):
    """'ollama-native' is no longer a supported provider."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(
        json.dumps({"llm_provider": "ollama-native"})
    )
    with pytest.raises(Exception, match="Unsupported llm_provider"):
        load_config(
            _user_config_path=tmp_path / "nonexistent.json",
            _project_dir=project_dir,
        )


def test_old_ollama_provider_string_rejected(tmp_path):
    """The bare 'ollama' discriminator is rejected after P1 rename."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(
        json.dumps({"llm_provider": "ollama"})
    )
    with pytest.raises(Exception, match="Unsupported llm_provider"):
        load_config(
            _user_config_path=tmp_path / "nonexistent.json",
            _project_dir=project_dir,
        )


def test_invalid_project_config_schema_names_file(tmp_path):
    """Schema validation failure must raise ValueError naming the project config file path."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    project_config_path = project_dir / ".co-cli" / "settings.json"
    project_config_path.write_text(json.dumps({"tool_retries": "not-an-int"}))

    with pytest.raises(ValueError, match=str(project_config_path)):
        load_config(
            _user_config_path=tmp_path / "nonexistent.json",
            _project_dir=project_dir,
        )


def test_get_settings_invalid_config_raises_system_exit(tmp_path, capsys):
    """get_settings() with schema-invalid project config raises SystemExit with clean message."""
    (tmp_path / ".co-cli").mkdir()
    (tmp_path / ".co-cli" / "settings.json").write_text(json.dumps({"tool_retries": "not-an-int"}))

    original_dir = Path.cwd()
    original_settings = config_module._settings
    # Reset singleton so get_settings() re-runs load_config()
    config_module._settings = None
    try:
        os.chdir(tmp_path)
        with pytest.raises(SystemExit):
            config_module.get_settings()
        captured = capsys.readouterr()
        assert "Configuration error:" in captured.err
    finally:
        os.chdir(original_dir)
        config_module._settings = original_settings


def test_gemini_api_key_not_written_to_env():
    """build_agent() must not mutate os.environ['GEMINI_API_KEY'] — key is injected via GoogleProvider."""
    import os
    from co_cli.agent import build_agent
    from co_cli.config import settings
    from co_cli.deps import CoConfig

    original_env = os.environ.get("GEMINI_API_KEY")
    original_key = settings.llm_api_key
    original_provider = settings.llm_provider
    try:
        os.environ["GEMINI_API_KEY"] = "stale-key-from-env"
        settings.llm_api_key = "settings-key-wins"
        settings.llm_provider = "gemini"
        build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
        # Key is passed directly to GoogleProvider — env var must be untouched
        assert os.environ["GEMINI_API_KEY"] == "stale-key-from-env"
    finally:
        settings.llm_api_key = original_key
        settings.llm_provider = original_provider
        if original_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = original_env
