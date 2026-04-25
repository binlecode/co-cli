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
        _env={"CO_THEME": "light"},
    )
    assert settings.theme == "light"


def test_missing_user_config_uses_defaults(tmp_path):
    """No user config — load_config() uses defaults."""
    settings = load_config(_user_config_path=tmp_path / "nonexistent.json")
    assert settings.theme == "light"


def test_malformed_user_config_raises(tmp_path):
    """Malformed user settings.json raises ValueError — fail fast, do not silently fall back."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text("not json{{{")

    with pytest.raises(ValueError, match="Malformed settings file"):
        load_config(_user_config_path=user_settings)


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


def test_default_provider_is_ollama(tmp_path):
    """When no llm_provider is set, the default must be 'ollama'."""
    settings = load_config(_user_config_path=tmp_path / "nonexistent.json")
    assert settings.llm.provider == "ollama"


def test_llm_model_loaded_from_user_config(tmp_path):
    """llm.model set in user config is reflected in settings."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"llm": {"model": "my-custom-model"}}))
    settings = load_config(_user_config_path=user_settings)
    assert settings.llm.model == "my-custom-model"


def test_ollama_native_provider_rejected(tmp_path):
    """'ollama-native' is not a supported provider."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"llm": {"provider": "ollama-native"}}))
    with pytest.raises(Exception, match=r"ollama.*gemini|literal_error"):
        load_config(_user_config_path=user_settings)


def test_ollama_openai_provider_string_rejected(tmp_path):
    """The old 'ollama-openai' provider name is no longer supported; use 'ollama'."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"llm": {"provider": "ollama-openai"}}))
    with pytest.raises(Exception, match=r"ollama.*gemini|literal_error"):
        load_config(_user_config_path=user_settings)


def test_invalid_config_schema_names_file(tmp_path):
    """Schema validation failure must raise ValueError naming the user config file path."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"tool_retries": "not-an-int"}))

    with pytest.raises(ValueError, match=str(user_settings)):
        load_config(_user_config_path=user_settings)


def test_knowledge_lifecycle_defaults(tmp_path):
    """Knowledge lifecycle fields have correct defaults when not set."""
    settings = load_config(_user_config_path=tmp_path / "nonexistent.json")
    assert settings.knowledge.consolidation_enabled is False
    assert settings.knowledge.consolidation_trigger == "session_end"
    assert settings.knowledge.consolidation_lookback_sessions == 5
    assert settings.knowledge.consolidation_similarity_threshold == 0.75
    assert settings.knowledge.max_artifact_count == 300
    assert settings.knowledge.decay_after_days == 90


def test_knowledge_lifecycle_env_overrides(tmp_path):
    """CO_KNOWLEDGE_CONSOLIDATION_ENABLED and CO_KNOWLEDGE_DECAY_AFTER_DAYS override defaults."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _env={"CO_KNOWLEDGE_CONSOLIDATION_ENABLED": "true", "CO_KNOWLEDGE_DECAY_AFTER_DAYS": "45"},
    )
    assert settings.knowledge.consolidation_enabled is True
    assert settings.knowledge.decay_after_days == 45


def test_memory_extract_every_n_turns_env_override(tmp_path):
    """CO_MEMORY_EXTRACT_EVERY_N_TURNS overrides the default extraction cadence."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _env={"CO_MEMORY_EXTRACT_EVERY_N_TURNS": "7"},
    )
    assert settings.memory.extract_every_n_turns == 7


def test_memory_extract_every_n_turns_default(tmp_path):
    """Without the env override, extract_every_n_turns keeps its documented default of 3."""
    settings = load_config(_user_config_path=tmp_path / "nonexistent.json")
    assert settings.memory.extract_every_n_turns == 3


def test_knowledge_lifecycle_from_user_config(tmp_path):
    """knowledge lifecycle fields can be set via settings.json."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps(
            {
                "knowledge": {
                    "consolidation_enabled": True,
                    "consolidation_trigger": "manual",
                    "consolidation_lookback_sessions": 10,
                    "consolidation_similarity_threshold": 0.85,
                    "max_artifact_count": 500,
                    "decay_after_days": 30,
                }
            }
        )
    )
    settings = load_config(_user_config_path=user_settings)
    assert settings.knowledge.consolidation_enabled is True
    assert settings.knowledge.consolidation_trigger == "manual"
    assert settings.knowledge.consolidation_lookback_sessions == 10
    assert settings.knowledge.consolidation_similarity_threshold == 0.85
    assert settings.knowledge.max_artifact_count == 500
    assert settings.knowledge.decay_after_days == 30


def test_knowledge_consolidation_trigger_invalid(tmp_path):
    """consolidation_trigger rejects values outside {session_end, manual}."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"knowledge": {"consolidation_trigger": "hourly"}}))
    with pytest.raises(ValueError, match=r"literal_error|consolidation_trigger"):
        load_config(_user_config_path=user_settings)


def test_llm_reranker_gemini_model_default_resolved(tmp_path):
    """LlmModelSettings fills gemini model from _RERANKER_DEFAULT_MODEL when omitted."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"knowledge": {"llm_reranker": {"provider": "gemini"}}}))
    settings = load_config(_user_config_path=user_settings)
    assert settings.knowledge.llm_reranker is not None
    assert settings.knowledge.llm_reranker.model == "gemini-3.1-flash-preview"


def test_llm_reranker_ollama_model_default_resolved(tmp_path):
    """LlmModelSettings fills ollama model from _RERANKER_DEFAULT_MODEL when omitted."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"knowledge": {"llm_reranker": {"provider": "ollama"}}}))
    settings = load_config(_user_config_path=user_settings)
    assert settings.knowledge.llm_reranker is not None
    assert settings.knowledge.llm_reranker.model == "qwen2.5:3b"


def test_llm_reranker_explicit_model_preserved(tmp_path):
    """Explicit model in settings.json is not overwritten by the default."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps(
            {"knowledge": {"llm_reranker": {"provider": "gemini", "model": "gemini-2.5-flash"}}}
        )
    )
    settings = load_config(_user_config_path=user_settings)
    assert settings.knowledge.llm_reranker is not None
    assert settings.knowledge.llm_reranker.model == "gemini-2.5-flash"


def test_llm_api_key_from_provider_specific_env(tmp_path):
    """GEMINI_API_KEY is picked up as llm.api_key when provider is gemini."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _env={"CO_LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "gemini-specific-key"},
    )
    assert settings.llm.api_key == "gemini-specific-key"


def test_llm_api_key_generic_fallback(tmp_path):
    """CO_LLM_API_KEY is used when no provider-specific env var is set."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _env={"CO_LLM_PROVIDER": "gemini", "CO_LLM_API_KEY": "generic-key"},
    )
    assert settings.llm.api_key == "generic-key"


def test_llm_api_key_provider_specific_wins_over_generic(tmp_path):
    """Provider-specific env var takes precedence over CO_LLM_API_KEY."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _env={
            "CO_LLM_PROVIDER": "gemini",
            "GEMINI_API_KEY": "specific-key",
            "CO_LLM_API_KEY": "generic-key",
        },
    )
    assert settings.llm.api_key == "specific-key"


def test_llm_api_key_absent_when_no_env_set(tmp_path):
    """llm.api_key remains None when neither provider-specific nor generic env var is set."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _env={},
    )
    assert settings.llm.api_key is None


def test_tools_settings_defaults(tmp_path):
    """ToolsSettings has correct defaults when not configured."""
    settings = load_config(_user_config_path=tmp_path / "nonexistent.json")
    assert settings.tools.result_persist_chars == 50_000
    assert settings.tools.batch_spill_chars == 200_000


def test_tools_result_persist_chars_env_override(tmp_path):
    """CO_TOOLS_RESULT_PERSIST_CHARS env var overrides result_persist_chars."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _env={"CO_TOOLS_RESULT_PERSIST_CHARS": "80000"},
    )
    assert settings.tools.result_persist_chars == 80_000


def test_tools_batch_spill_chars_env_override(tmp_path):
    """CO_TOOLS_BATCH_SPILL_CHARS env var overrides batch_spill_chars."""
    settings = load_config(
        _user_config_path=tmp_path / "nonexistent.json",
        _env={"CO_TOOLS_BATCH_SPILL_CHARS": "300000"},
    )
    assert settings.tools.batch_spill_chars == 300_000


def test_tools_settings_from_user_config(tmp_path):
    """tools fields can be set via settings.json."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps({"tools": {"result_persist_chars": 75_000, "batch_spill_chars": 500_000}})
    )
    settings = load_config(_user_config_path=user_settings)
    assert settings.tools.result_persist_chars == 75_000
    assert settings.tools.batch_spill_chars == 500_000


def test_build_agent_does_not_mutate_gemini_api_key_env(tmp_path):
    """build_agent() must not rewrite GEMINI_API_KEY when config provides llm_api_key."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps(
            {
                "llm": {
                    "provider": "gemini",
                    "model": "gemini-3.1-flash-preview",
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


def test_tools_result_persist_chars_below_minimum_raises(tmp_path):
    """result_persist_chars below ge=1_000 must be rejected by the validator."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"tools": {"result_persist_chars": 500}}))
    with pytest.raises(ValueError, match=r"result_persist_chars|greater_than_equal"):
        load_config(_user_config_path=user_settings)


def test_tools_batch_spill_chars_below_minimum_raises(tmp_path):
    """batch_spill_chars below ge=10_000 must be rejected by the validator."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"tools": {"batch_spill_chars": 1_000}}))
    with pytest.raises(ValueError, match=r"batch_spill_chars|greater_than_equal"):
        load_config(_user_config_path=user_settings)


def test_compaction_proactive_ratio_at_or_above_hygiene_rejected(tmp_path):
    """proactive_ratio >= hygiene_ratio inverts trigger ordering and must fail at load time."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps({"compaction": {"proactive_ratio": 0.90, "hygiene_ratio": 0.88}})
    )
    with pytest.raises(ValueError, match=r"proactive_ratio.*hygiene_ratio|strictly less"):
        load_config(_user_config_path=user_settings)


def test_compaction_equal_ratios_rejected(tmp_path):
    """Equal proactive and hygiene ratios collapse the safety-net layer and must fail."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps({"compaction": {"proactive_ratio": 0.80, "hygiene_ratio": 0.80}})
    )
    with pytest.raises(ValueError, match=r"proactive_ratio.*hygiene_ratio|strictly less"):
        load_config(_user_config_path=user_settings)


def test_compaction_valid_ratio_ordering_loads(tmp_path):
    """Default ordering (proactive < hygiene) must continue to load cleanly."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps({"compaction": {"proactive_ratio": 0.70, "hygiene_ratio": 0.85}})
    )
    settings = load_config(_user_config_path=user_settings)
    assert settings.compaction.proactive_ratio == 0.70
    assert settings.compaction.hygiene_ratio == 0.85


def test_unknown_top_level_key_rejected(tmp_path):
    """Unknown top-level key in settings.json must fail with file attribution, not silently drop."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"thme": "dark"}))
    with pytest.raises(ValueError, match=str(user_settings)):
        load_config(_user_config_path=user_settings)


def test_unknown_nested_key_rejected(tmp_path):
    """Unknown key inside a nested group must fail with file attribution, not silently drop."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(json.dumps({"llm": {"modle": "my-model"}}))
    with pytest.raises(ValueError, match=str(user_settings)):
        load_config(_user_config_path=user_settings)


def test_unknown_mcp_server_key_rejected(tmp_path):
    """Unknown key in an MCP server entry must fail, not silently drop."""
    user_settings = tmp_path / "settings.json"
    user_settings.write_text(
        json.dumps({"mcp_servers": {"my-server": {"command": "npx", "typo_key": "value"}}})
    )
    with pytest.raises(ValueError, match=str(user_settings)):
        load_config(_user_config_path=user_settings)
