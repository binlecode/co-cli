"""Functional tests for provider and model availability checks."""

from co_cli.bootstrap.check import (
    check_agent_llm,
    check_cross_encoder,
    check_embedder,
    check_ollama_model,
    check_reranker_llm,
)
from co_cli.config._core import Settings
from co_cli.config._knowledge import KnowledgeSettings, LlmModelSettings
from co_cli.config._llm import LlmSettings

# --- LlmSettings.validate_config() (config-shape gate, no IO) ---


def test_validate_empty_model_returns_error() -> None:
    error = LlmSettings.model_construct(model="").validate_config()
    assert error is not None
    assert "model" in error.lower()


def test_validate_gemini_no_key_returns_error() -> None:
    llm = LlmSettings.model_construct(
        model="test",
        provider="gemini",
        api_key=None,
    )
    error = llm.validate_config()
    assert error is not None
    assert "LLM_API_KEY" in error


def test_validate_gemini_with_key_returns_ok() -> None:
    llm = LlmSettings.model_construct(
        model="test",
        provider="gemini",
        api_key="key",
    )
    assert llm.validate_config() is None


def test_validate_ollama_returns_ok_without_io() -> None:
    """Ollama config with model configured passes instantly — no HTTP probe."""
    llm = LlmSettings.model_construct(
        model="test",
        provider="ollama-openai",
    )
    assert llm.validate_config() is None


# --- check_agent_llm (IO probe, runtime diagnostics) ---


def test_check_agent_llm_gemini_key_missing_returns_error() -> None:
    config = Settings.model_construct(
        llm=LlmSettings.model_construct(provider="gemini", api_key=None),
    )
    result = check_agent_llm(config)
    assert result.status == "error"
    assert not result.ok
    assert "LLM_API_KEY" in result.detail


def test_check_agent_llm_gemini_key_present_returns_ok() -> None:
    config = Settings.model_construct(
        llm=LlmSettings.model_construct(provider="gemini", api_key="test-key"),
    )
    result = check_agent_llm(config)
    assert result.status == "ok"
    assert result.ok


def test_check_agent_llm_ollama_unreachable_returns_warn() -> None:
    # Port 1 is reserved/unreachable — connection refused immediately.
    config = Settings.model_construct(
        llm=LlmSettings.model_construct(provider="ollama-openai", host="http://localhost:1"),
    )
    result = check_agent_llm(config)
    assert result.status == "warn"
    assert result.ok


def test_check_agent_llm_ollama_unreachable_stamps_reason_unreachable() -> None:
    """Unreachable host must set extra['reason']='unreachable' so get_status maps it to 'offline', not 'online'."""
    config = Settings.model_construct(
        llm=LlmSettings.model_construct(provider="ollama-openai", host="http://localhost:1"),
    )
    result = check_agent_llm(config)
    assert result.extra.get("reason") == "unreachable"


# --- check_ollama_model ---


def test_check_ollama_model_unreachable_returns_warn() -> None:
    result = check_ollama_model("http://localhost:1", "any-model")
    assert result.status == "warn"
    assert result.ok


# --- check_reranker_llm ---


def test_check_reranker_llm_not_configured_returns_skipped() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(llm_reranker=None),
    )
    result = check_reranker_llm(config)
    assert result.status == "skipped"


def test_check_reranker_llm_gemini_no_key_returns_error() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(
            llm_reranker=LlmModelSettings(provider="gemini", model="gemini-2.0-flash"),
        ),
        llm=LlmSettings.model_construct(provider="gemini", api_key=None),
    )
    result = check_reranker_llm(config)
    assert result.status == "error"
    assert not result.ok


def test_check_reranker_llm_gemini_with_key_returns_ok() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(
            llm_reranker=LlmModelSettings(provider="gemini", model="gemini-2.0-flash"),
        ),
        llm=LlmSettings.model_construct(provider="gemini", api_key="test-key"),
    )
    result = check_reranker_llm(config)
    assert result.status == "ok"
    assert result.ok


def test_check_reranker_llm_ollama_unreachable_returns_warn() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(
            llm_reranker=LlmModelSettings(provider="ollama-openai", model="reranker-model"),
        ),
        llm=LlmSettings.model_construct(provider="ollama-openai", host="http://localhost:1"),
    )
    result = check_reranker_llm(config)
    # unreachable host → warn (ok=True), caller must degrade because status != "ok"
    assert result.status == "warn"
    assert result.ok


# --- check_embedder ---


def test_check_embedder_provider_none_returns_skipped() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(embedding_provider="none"),
    )
    result = check_embedder(config)
    assert result.status == "skipped"


def test_check_embedder_tei_unreachable_returns_error() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(
            embedding_provider="tei",
            embed_api_url="http://localhost:1/embed",
        ),
    )
    result = check_embedder(config)
    assert result.status == "error"
    assert not result.ok


def test_check_embedder_ollama_unreachable_returns_warn() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(
            embedding_provider="ollama",
            embedding_model="nomic-embed-text",
        ),
        llm=LlmSettings.model_construct(host="http://localhost:1"),
    )
    result = check_embedder(config)
    assert result.status == "warn"
    assert result.ok


def test_check_embedder_gemini_no_key_returns_error() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(embedding_provider="gemini"),
        llm=LlmSettings.model_construct(api_key=None),
    )
    result = check_embedder(config)
    assert result.status == "error"
    assert not result.ok


# --- check_cross_encoder ---


def test_check_cross_encoder_not_configured_returns_skipped() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(cross_encoder_reranker_url=None),
    )
    result = check_cross_encoder(config)
    assert result.status == "skipped"


def test_check_cross_encoder_unreachable_returns_error() -> None:
    config = Settings.model_construct(
        knowledge=KnowledgeSettings.model_construct(
            cross_encoder_reranker_url="http://localhost:1/rerank"
        ),
    )
    result = check_cross_encoder(config)
    assert result.status == "error"
    assert not result.ok
