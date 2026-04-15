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


def test_env_overrides_user_config(tmp_path):
    """Environment variables override user config."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"theme": "dark"}))

    settings = load_config(
        _user_config_path=user_settings,
        _env={"CO_CLI_THEME": "light"},
    )
    assert settings.theme == "light"


def test_missing_user_config_uses_defaults(tmp_path):
    """No user config — load_config() uses defaults."""
    settings = load_config(_user_config_path=tmp_path / "nonexistent.json")
    assert settings.theme == "light"


def test_malformed_user_config_skipped(tmp_path, capsys):
    """Malformed user settings.json is skipped gracefully, falling back to defaults."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text("not json{{{")

    settings = load_config(_user_config_path=user_settings)
    assert settings.theme == "light"
    assert "Error loading settings.json" in capsys.readouterr().out


def test_knowledge_llm_reranker_missing_provider_rejected(tmp_path):
    """knowledge_llm_reranker must specify provider explicitly in config files."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps({"knowledge": {"llm_reranker": {"model": "gemini-2.0-flash"}}})
    )

    with pytest.raises(ValueError, match="provider"):
        load_config(_user_config_path=user_settings)


def test_invalid_web_retry_bounds_raise_value_error(tmp_path):
    """Invalid retry bounds must fail through the real config loader with file attribution."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps({"web": {"http_backoff_base_seconds": 10.0, "http_backoff_max_seconds": 1.0}})
    )

    with pytest.raises(ValueError, match=str(user_settings)):
        load_config(_user_config_path=user_settings)


def test_invalid_personality_raises_value_error(tmp_path):
    """Invalid personality values fail through load_config instead of direct model construction."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"personality": "invalid"}))

    with pytest.raises(ValueError, match=str(user_settings)):
        load_config(_user_config_path=user_settings)


def test_default_provider_is_ollama_openai(tmp_path):
    """When no llm_provider is set, the default must be 'ollama-openai' (P1 rename)."""
    settings = load_config(_user_config_path=tmp_path / "nonexistent.json")
    assert settings.llm.provider == "ollama-openai"


def test_llm_model_loaded_from_user_config(tmp_path):
    """llm.model set in user config is reflected in settings."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"llm": {"model": "my-custom-model"}}))
    settings = load_config(_user_config_path=user_settings)
    assert settings.llm.model == "my-custom-model"


def test_ollama_native_provider_rejected(tmp_path):
    """'ollama-native' is no longer a supported provider."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"llm": {"provider": "ollama-native"}}))
    with pytest.raises(Exception, match=r"ollama-openai.*gemini|literal_error"):
        load_config(_user_config_path=user_settings)


def test_old_ollama_provider_string_rejected(tmp_path):
    """The bare 'ollama' discriminator is rejected after P1 rename."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"llm": {"provider": "ollama"}}))
    with pytest.raises(Exception, match=r"ollama-openai.*gemini|literal_error"):
        load_config(_user_config_path=user_settings)


def test_invalid_config_schema_names_file(tmp_path):
    """Schema validation failure must raise ValueError naming the user config file path."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"tool_retries": "not-an-int"}))

    with pytest.raises(ValueError, match=str(user_settings)):
        load_config(_user_config_path=user_settings)


def test_build_agent_does_not_mutate_gemini_api_key_env(tmp_path):
    """build_agent() must not rewrite GEMINI_API_KEY when config provides llm_api_key."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps({"llm": {"provider": "gemini", "api_key": "settings-key-wins"}})
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
                f"loaded = load_config(_user_config_path=Path({str(user_settings)!r})); "
                "build_agent(config=loaded); "
                "print(os.environ['GEMINI_API_KEY'])"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert proc.stdout.strip() == "stale-key-from-env"
