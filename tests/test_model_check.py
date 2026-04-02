"""Functional tests for provider and model availability checks."""

from co_cli.bootstrap._check import (
    check_agent_llm,
    check_ollama_model,
    check_reranker_llm,
    check_embedder,
    check_cross_encoder,
)
from co_cli.config import ModelConfig, ROLE_REASONING
from co_cli.deps import CoConfig


# --- CoConfig.validate() (config-shape gate, no IO) ---


def test_validate_no_reasoning_model_returns_error() -> None:
    error = CoConfig(role_models={}).validate()
    assert error is not None
    assert "reasoning" in error.lower()


def test_validate_gemini_no_key_returns_error() -> None:
    config = CoConfig(
        llm_provider="gemini",
        llm_api_key=None,
        role_models={ROLE_REASONING: ModelConfig(provider="gemini", model="gemini-2.5-flash")},
    )
    error = config.validate()
    assert error is not None
    assert "LLM_API_KEY" in error


def test_validate_gemini_with_key_returns_ok() -> None:
    config = CoConfig(
        llm_provider="gemini",
        llm_api_key="test-key",
        role_models={ROLE_REASONING: ModelConfig(provider="gemini", model="gemini-2.5-flash")},
    )
    assert config.validate() is None


def test_validate_ollama_returns_ok_without_io() -> None:
    """Ollama config with reasoning model configured passes instantly — no HTTP probe."""
    config = CoConfig(
        llm_provider="ollama-openai",
        role_models={ROLE_REASONING: ModelConfig(provider="ollama-openai", model="qwen3:8b")},
    )
    assert config.validate() is None


# --- check_agent_llm (IO probe, runtime diagnostics) ---


def test_check_agent_llm_gemini_key_missing_returns_error() -> None:
    result = check_agent_llm(CoConfig(llm_provider="gemini", llm_api_key=None))
    assert result.status == "error"
    assert not result.ok
    assert "LLM_API_KEY" in result.detail


def test_check_agent_llm_gemini_key_present_returns_ok() -> None:
    result = check_agent_llm(CoConfig(llm_provider="gemini", llm_api_key="test-key"))
    assert result.status == "ok"
    assert result.ok


def test_check_agent_llm_ollama_unreachable_returns_warn() -> None:
    # Port 1 is reserved/unreachable — connection refused immediately.
    result = check_agent_llm(CoConfig(llm_provider="ollama-openai", llm_host="http://localhost:1"))
    assert result.status == "warn"
    assert result.ok


def test_check_agent_llm_ollama_unreachable_stamps_reason_unreachable() -> None:
    """Unreachable host must set extra['reason']='unreachable' so get_status maps it to 'offline', not 'online'."""
    result = check_agent_llm(CoConfig(llm_provider="ollama-openai", llm_host="http://localhost:1"))
    assert result.extra.get("reason") == "unreachable"


# --- check_ollama_model ---

def test_check_ollama_model_unreachable_returns_warn() -> None:
    result = check_ollama_model("http://localhost:1", "any-model")
    assert result.status == "warn"
    assert result.ok


# --- check_reranker_llm ---

def test_check_reranker_llm_not_configured_returns_skipped() -> None:
    config = CoConfig(knowledge_llm_reranker=None)
    result = check_reranker_llm(config)
    assert result.status == "skipped"


def test_check_reranker_llm_gemini_no_key_returns_error() -> None:
    config = CoConfig(
        knowledge_llm_reranker=ModelConfig(provider="gemini", model="gemini-2.0-flash"),
        llm_provider="gemini",
        llm_api_key=None,
    )
    result = check_reranker_llm(config)
    assert result.status == "error"
    assert not result.ok


def test_check_reranker_llm_gemini_with_key_returns_ok() -> None:
    config = CoConfig(
        knowledge_llm_reranker=ModelConfig(provider="gemini", model="gemini-2.0-flash"),
        llm_provider="gemini",
        llm_api_key="test-key",
    )
    result = check_reranker_llm(config)
    assert result.status == "ok"
    assert result.ok


def test_check_reranker_llm_ollama_unreachable_returns_warn() -> None:
    config = CoConfig(
        knowledge_llm_reranker=ModelConfig(provider="ollama-openai", model="reranker-model"),
        llm_provider="ollama-openai",
        llm_host="http://localhost:1",
    )
    result = check_reranker_llm(config)
    # unreachable host → warn (ok=True), caller must degrade because status != "ok"
    assert result.status == "warn"
    assert result.ok


# --- check_embedder ---

def test_check_embedder_provider_none_returns_skipped() -> None:
    config = CoConfig(knowledge_embedding_provider="none")
    result = check_embedder(config)
    assert result.status == "skipped"


def test_check_embedder_tei_unreachable_returns_error() -> None:
    config = CoConfig(
        knowledge_embedding_provider="tei",
        knowledge_embed_api_url="http://localhost:1/embed",
    )
    result = check_embedder(config)
    assert result.status == "error"
    assert not result.ok


def test_check_embedder_ollama_unreachable_returns_warn() -> None:
    config = CoConfig(
        knowledge_embedding_provider="ollama",
        knowledge_embedding_model="nomic-embed-text",
        llm_host="http://localhost:1",
    )
    result = check_embedder(config)
    assert result.status == "warn"
    assert result.ok


def test_check_embedder_gemini_no_key_returns_error() -> None:
    config = CoConfig(
        knowledge_embedding_provider="gemini",
        llm_api_key=None,
    )
    result = check_embedder(config)
    assert result.status == "error"
    assert not result.ok


# --- check_cross_encoder ---

def test_check_cross_encoder_not_configured_returns_skipped() -> None:
    config = CoConfig(knowledge_cross_encoder_reranker_url=None)
    result = check_cross_encoder(config)
    assert result.status == "skipped"


def test_check_cross_encoder_unreachable_returns_error() -> None:
    config = CoConfig(knowledge_cross_encoder_reranker_url="http://localhost:1/rerank")
    result = check_cross_encoder(config)
    assert result.status == "error"
    assert not result.ok
