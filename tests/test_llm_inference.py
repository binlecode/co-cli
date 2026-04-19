"""Tests for LLM inference settings resolution — model quirks contract.

Covers: normalize_model_name, _merge_inference, resolve_reasoning_inference,
_MODEL_REASONING_DEFAULTS, resolve_noreason_inference, LlmSettings inference methods.

These are pure-logic tests (no IO, no model construction) that verify the
config/runtime contract documented in docs/specs/llm-models.md.
"""

from co_cli.config._llm import (
    DEFAULT_NOREASON_EXTRA_BODY,
    DEFAULT_NOREASON_MAX_TOKENS,
    DEFAULT_NOREASON_TEMPERATURE,
    DEFAULT_NOREASON_TOP_P,
    DEFAULT_OLLAMA_MAX_TOKENS,
    DEFAULT_OLLAMA_TEMPERATURE,
    DEFAULT_OLLAMA_TOP_P,
    LlmSettings,
    NoReasonSettings,
    ReasoningSettings,
    _merge_inference,
    normalize_model_name,
    resolve_noreason_inference,
    resolve_reasoning_inference,
)

# ---------------------------------------------------------------------------
# normalize_model_name
# ---------------------------------------------------------------------------


def test_normalize_strips_quantization_tag() -> None:
    assert normalize_model_name("qwen3.5:35b-a3b-think") == "qwen3.5"


def test_normalize_strips_simple_tag() -> None:
    assert normalize_model_name("qwen3:1b") == "qwen3"


def test_normalize_no_colon_unchanged() -> None:
    assert normalize_model_name("gemini-3-flash-preview") == "gemini-3-flash-preview"


def test_normalize_empty_string_unchanged() -> None:
    assert normalize_model_name("") == ""


# ---------------------------------------------------------------------------
# _merge_inference
# ---------------------------------------------------------------------------


def test_merge_inference_override_wins() -> None:
    base = {"temperature": 0.7, "top_p": 1.0, "max_tokens": 16384}
    override = {"temperature": 1.0}
    result = _merge_inference(base, override)
    assert result["temperature"] == 1.0
    assert result["top_p"] == 1.0


def test_merge_inference_extra_body_shallow_merge() -> None:
    base = {"extra_body": {"top_k": 20, "min_p": 0.0}}
    override = {"extra_body": {"presence_penalty": 1.5}}
    result = _merge_inference(base, override)
    assert result["extra_body"] == {"top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}


def test_merge_inference_override_extra_body_key_wins() -> None:
    base = {"extra_body": {"top_k": 20}}
    override = {"extra_body": {"top_k": 40}}
    result = _merge_inference(base, override)
    assert result["extra_body"]["top_k"] == 40


def test_merge_inference_empty_extra_body_dropped() -> None:
    base = {"temperature": 0.7}
    override = {"extra_body": {}}
    result = _merge_inference(base, override)
    assert "extra_body" not in result


def test_merge_inference_base_extra_body_preserved_when_override_has_none() -> None:
    base = {"temperature": 0.7, "extra_body": {"top_k": 20}}
    override = {"temperature": 1.0}
    result = _merge_inference(base, override)
    assert result["extra_body"] == {"top_k": 20}


# ---------------------------------------------------------------------------
# resolve_reasoning_inference — provider defaults
# ---------------------------------------------------------------------------


def test_resolve_ollama_provider_defaults_for_unknown_model() -> None:
    llm = LlmSettings.model_construct(provider="ollama-openai", model="unknown-model:4b")
    inference = resolve_reasoning_inference(llm)
    assert inference["temperature"] == DEFAULT_OLLAMA_TEMPERATURE
    assert inference["top_p"] == DEFAULT_OLLAMA_TOP_P
    assert inference["max_tokens"] == DEFAULT_OLLAMA_MAX_TOKENS


# ---------------------------------------------------------------------------
# resolve_reasoning_inference — _MODEL_REASONING_DEFAULTS (model quirks)
# ---------------------------------------------------------------------------


def test_resolve_qwen35_overrides_provider_defaults() -> None:
    llm = LlmSettings.model_construct(provider="ollama-openai", model="qwen3.5:35b-a3b-think")
    inference = resolve_reasoning_inference(llm)
    # qwen3.5 default: temperature=1.0, overrides provider default 0.7
    assert inference["temperature"] == 1.0
    assert inference["top_p"] == 0.95
    assert inference["max_tokens"] == 32768
    assert inference["context_window"] == 262144


def test_resolve_qwen35_extra_body_presence_penalty() -> None:
    llm = LlmSettings.model_construct(provider="ollama-openai", model="qwen3.5:35b-a3b-think")
    inference = resolve_reasoning_inference(llm)
    assert inference.get("extra_body", {}).get("presence_penalty") == 1.5


def test_resolve_qwen3_overrides_provider_defaults() -> None:
    llm = LlmSettings.model_construct(provider="ollama-openai", model="qwen3:1b")
    inference = resolve_reasoning_inference(llm)
    assert inference["temperature"] == 0.6
    assert inference["top_p"] == 0.95
    assert inference["max_tokens"] == 32768
    assert inference["context_window"] == 262144


def test_resolve_gemini_flash_context_window() -> None:
    llm = LlmSettings.model_construct(provider="gemini", model="gemini-3-flash-preview")
    inference = resolve_reasoning_inference(llm)
    assert inference["temperature"] == 1.0
    assert inference["max_tokens"] == 65536
    assert inference["context_window"] == 1048576


def test_resolve_gemini_pro_context_window() -> None:
    llm = LlmSettings.model_construct(provider="gemini", model="gemini-3-pro-preview")
    inference = resolve_reasoning_inference(llm)
    assert inference["context_window"] == 1048576


# ---------------------------------------------------------------------------
# resolve_reasoning_inference — explicit ReasoningSettings overrides
# ---------------------------------------------------------------------------


def test_resolve_explicit_override_beats_model_default() -> None:
    llm = LlmSettings.model_construct(
        provider="ollama-openai",
        model="qwen3.5:35b-a3b-think",
        reasoning=ReasoningSettings(temperature=0.3),
    )
    inference = resolve_reasoning_inference(llm)
    assert inference["temperature"] == 0.3
    # Other fields still from model default
    assert inference["top_p"] == 0.95


def test_resolve_explicit_context_window_beats_model_default() -> None:
    llm = LlmSettings.model_construct(
        provider="ollama-openai",
        model="qwen3.5:35b-a3b-think",
        reasoning=ReasoningSettings(context_window=131072),
    )
    inference = resolve_reasoning_inference(llm)
    assert inference["context_window"] == 131072


# ---------------------------------------------------------------------------
# LlmSettings.reasoning_model_settings() and reasoning_context_window()
# ---------------------------------------------------------------------------


def test_reasoning_model_settings_qwen35_returns_correct_temperature() -> None:
    llm = LlmSettings.model_construct(provider="ollama-openai", model="qwen3.5:35b-a3b-think")
    ms = llm.reasoning_model_settings()
    assert ms.get("temperature") == 1.0


def test_reasoning_context_window_qwen35() -> None:
    llm = LlmSettings.model_construct(provider="ollama-openai", model="qwen3.5:35b-a3b-think")
    assert llm.reasoning_context_window() == 262144


def test_reasoning_context_window_unknown_model_returns_none() -> None:
    llm = LlmSettings.model_construct(provider="ollama-openai", model="unknown-model")
    assert llm.reasoning_context_window() is None


def test_reasoning_context_window_gemini_flash() -> None:
    llm = LlmSettings.model_construct(provider="gemini", model="gemini-3-flash-preview")
    assert llm.reasoning_context_window() == 1048576


# ---------------------------------------------------------------------------
# NoReasonSettings is a pure-override model — all fields default to None/empty
# ---------------------------------------------------------------------------


def test_noreason_settings_model_defaults_match_constants() -> None:
    ns = NoReasonSettings()
    assert ns.temperature is None
    assert ns.top_p is None
    assert ns.max_tokens is None
    assert ns.extra_body == {}


# ---------------------------------------------------------------------------
# resolve_noreason_inference — provider defaults
# ---------------------------------------------------------------------------


def test_resolve_noreason_ollama_yields_full_key_set() -> None:
    llm = LlmSettings.model_construct(
        provider="ollama-openai", model="qwen3.5:35b-a3b-think", noreason=NoReasonSettings()
    )
    inference = resolve_noreason_inference(llm)
    assert inference["temperature"] == DEFAULT_NOREASON_TEMPERATURE
    assert inference["top_p"] == DEFAULT_NOREASON_TOP_P
    assert inference["max_tokens"] == DEFAULT_NOREASON_MAX_TOKENS
    assert inference.get("extra_body", {}).get("reasoning_effort") == "none"


def test_resolve_noreason_ollama_extra_body_matches_constant() -> None:
    llm = LlmSettings.model_construct(
        provider="ollama-openai", model="qwen3.5:35b-a3b-think", noreason=NoReasonSettings()
    )
    inference = resolve_noreason_inference(llm)
    for key, value in DEFAULT_NOREASON_EXTRA_BODY.items():
        assert inference.get("extra_body", {}).get(key) == value, f"extra_body[{key!r}] mismatch"


def test_resolve_noreason_gemini_flash_thinking_level_minimal() -> None:
    llm = LlmSettings.model_construct(
        provider="gemini", model="gemini-3-flash-preview", noreason=NoReasonSettings()
    )
    inference = resolve_noreason_inference(llm)
    assert inference.get("thinking_config") == {"thinking_level": "minimal"}


def test_resolve_noreason_gemini_pro_thinking_level_low() -> None:
    llm = LlmSettings.model_construct(
        provider="gemini", model="gemini-3-pro-preview", noreason=NoReasonSettings()
    )
    inference = resolve_noreason_inference(llm)
    assert inference.get("thinking_config") == {"thinking_level": "low"}


def test_resolve_noreason_gemini_25flash_thinking_budget_zero() -> None:
    llm = LlmSettings.model_construct(
        provider="gemini", model="gemini-2.5-flash", noreason=NoReasonSettings()
    )
    inference = resolve_noreason_inference(llm)
    assert inference.get("thinking_config") == {"thinking_budget": 0}


def test_resolve_noreason_gemini_25flash_lite_thinking_budget_zero() -> None:
    llm = LlmSettings.model_construct(
        provider="gemini", model="gemini-2.5-flash-lite", noreason=NoReasonSettings()
    )
    inference = resolve_noreason_inference(llm)
    assert inference.get("thinking_config") == {"thinking_budget": 0}


def test_resolve_noreason_user_explicit_temperature_wins() -> None:
    llm = LlmSettings.model_construct(
        provider="ollama-openai",
        model="qwen3.5:35b-a3b-think",
        noreason=NoReasonSettings(temperature=0.3),
    )
    inference = resolve_noreason_inference(llm)
    assert inference["temperature"] == 0.3
    # Provider defaults still apply for other fields
    assert inference["top_p"] == DEFAULT_NOREASON_TOP_P


def test_resolve_noreason_user_explicit_extra_body_wins() -> None:
    llm = LlmSettings.model_construct(
        provider="ollama-openai",
        model="qwen3.5:35b-a3b-think",
        noreason=NoReasonSettings(extra_body={"custom": 1}),
    )
    inference = resolve_noreason_inference(llm)
    assert inference.get("extra_body", {}).get("custom") == 1
    # Provider defaults still layer under
    assert inference.get("extra_body", {}).get("reasoning_effort") == "none"


# ---------------------------------------------------------------------------
# LlmSettings.noreason_model_settings() — provider-aware construction
# ---------------------------------------------------------------------------


def test_noreason_model_settings_ollama_has_extra_body_no_thinking_config() -> None:
    llm = LlmSettings.model_construct(
        provider="ollama-openai", model="qwen3.5:35b-a3b-think", noreason=NoReasonSettings()
    )
    ms = llm.noreason_model_settings()
    assert ms.get("extra_body", {}).get("reasoning_effort") == "none"
    assert "google_thinking_config" not in (ms or {})


def test_noreason_model_settings_gemini_flash_has_google_thinking_config() -> None:
    llm = LlmSettings.model_construct(
        provider="gemini",
        model="gemini-3-flash-preview",
        noreason=NoReasonSettings(),
        api_key="test",
    )
    ms = llm.noreason_model_settings()
    assert ms.get("google_thinking_config") == {"thinking_level": "minimal"}
    assert "extra_body" not in (ms or {})


def test_noreason_model_settings_ollama_matches_noreason_settings_constant() -> None:
    llm = LlmSettings.model_construct(
        provider="ollama-openai", model="qwen3.5:35b-a3b-think", noreason=NoReasonSettings()
    )
    ms = llm.noreason_model_settings()
    assert ms.get("temperature") == DEFAULT_NOREASON_TEMPERATURE
    assert ms.get("top_p") == DEFAULT_NOREASON_TOP_P
    assert ms.get("max_tokens") == DEFAULT_NOREASON_MAX_TOKENS
    assert ms.get("extra_body") == DEFAULT_NOREASON_EXTRA_BODY
