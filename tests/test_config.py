"""Functional tests for configuration precedence and validation.

Tests exercise real load_config() — no mocks.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from co_cli.config._core import load_config


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


def test_project_config_partially_overrides_mcp_server(tmp_path):
    """Project config can override one MCP server field without redefining the whole server."""
    user_settings = tmp_path / "user" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(
        json.dumps(
            {
                "mcp_servers": {
                    "github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "approval": "ask",
                    }
                }
            }
        )
    )

    project_dir = tmp_path / "project"
    (project_dir / ".co-cli").mkdir(parents=True)
    (project_dir / ".co-cli" / "settings.json").write_text(
        json.dumps(
            {
                "mcp_servers": {
                    "github": {
                        "approval": "auto",
                    }
                }
            }
        )
    )

    settings = load_config(_user_config_path=user_settings, _project_dir=project_dir)
    github = settings.mcp_servers["github"]
    assert github.command == "npx"
    assert github.args == ["-y", "@modelcontextprotocol/server-github"]
    assert github.approval == "auto"


def test_knowledge_llm_reranker_missing_provider_rejected(tmp_path):
    """knowledge_llm_reranker must specify provider explicitly in config files."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    project_config_path = project_dir / ".co-cli" / "settings.json"
    project_config_path.write_text(
        json.dumps(
            {
                "knowledge": {
                    "llm_reranker": {
                        "model": "gemini-2.0-flash",
                    }
                }
            }
        )
    )

    with pytest.raises(ValueError, match="provider"):
        load_config(
            _user_config_path=tmp_path / "nonexistent.json",
            _project_dir=project_dir,
        )


def test_invalid_web_retry_bounds_in_project_config_raise_value_error(tmp_path):
    """Invalid retry bounds must fail through the real config loader with file attribution."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    project_config_path = project_dir / ".co-cli" / "settings.json"
    project_config_path.write_text(
        json.dumps(
            {
                "web": {
                    "http_backoff_base_seconds": 10.0,
                    "http_backoff_max_seconds": 1.0,
                }
            }
        )
    )

    with pytest.raises(ValueError, match=str(project_config_path)):
        load_config(
            _user_config_path=tmp_path / "nonexistent.json",
            _project_dir=project_dir,
        )


def test_invalid_personality_in_project_config_raises_value_error(tmp_path):
    """Invalid personality values fail through load_config instead of direct model construction."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    project_config_path = project_dir / ".co-cli" / "settings.json"
    project_config_path.write_text(json.dumps({"personality": "invalid"}))

    with pytest.raises(ValueError, match=str(project_config_path)):
        load_config(
            _user_config_path=tmp_path / "nonexistent.json",
            _project_dir=project_dir,
        )


def test_default_provider_is_ollama_openai(tmp_path):
    """When no llm_provider is set, the default must be 'ollama-openai' (P1 rename)."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=tmp_path / "empty",
    )
    assert settings.llm.provider == "ollama-openai"


def test_llm_model_loaded_from_project_config(tmp_path):
    """llm.model set in project config is reflected in settings — replaces old role_models.reasoning path."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(
        json.dumps({"llm": {"model": "my-custom-model"}})
    )
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _project_dir=project_dir,
    )
    assert settings.llm.model == "my-custom-model"


def test_ollama_native_provider_rejected(tmp_path):
    """'ollama-native' is no longer a supported provider."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(
        json.dumps({"llm": {"provider": "ollama-native"}})
    )
    with pytest.raises(Exception, match=r"ollama-openai.*gemini|literal_error"):
        load_config(
            _user_config_path=tmp_path / "nonexistent.json",
            _project_dir=project_dir,
        )


def test_old_ollama_provider_string_rejected(tmp_path):
    """The bare 'ollama' discriminator is rejected after P1 rename."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(
        json.dumps({"llm": {"provider": "ollama"}})
    )
    with pytest.raises(Exception, match=r"ollama-openai.*gemini|literal_error"):
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


def test_build_agent_does_not_mutate_gemini_api_key_env(tmp_path):
    """build_agent() must not rewrite GEMINI_API_KEY when config provides llm_api_key."""
    project_dir = tmp_path
    (project_dir / ".co-cli").mkdir()
    (project_dir / ".co-cli" / "settings.json").write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "gemini",
                    "api_key": "settings-key-wins",
                }
            }
        )
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path.cwd())
    env["GEMINI_API_KEY"] = "stale-key-from-env"

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from co_cli.agent._core import build_agent; "
                "from co_cli.config._core import load_config; "
                "import os; "
                "loaded = load_config(_user_config_path=Path('missing.json'), _project_dir=Path.cwd()); "
                "build_agent(config=loaded); "
                "print(os.environ['GEMINI_API_KEY'])"
            ),
        ],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert proc.stdout.strip() == "stale-key-from-env"
