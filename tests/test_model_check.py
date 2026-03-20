"""Functional tests for provider and model availability checks."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from co_cli.bootstrap._check import (
    check_agent_llm,
    check_ollama_model,
    check_reranker_llm,
    check_embedder,
    check_cross_encoder,
)
from co_cli.config import ModelEntry, ROLE_REASONING
from co_cli.deps import CoConfig


def _make_ollama_server(models: list[str]) -> tuple[HTTPServer, int]:
    """Spin up a minimal /api/tags HTTP server serving the given model names."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    payload = json.dumps({"models": [{"name": m} for m in models]}).encode()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


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


def test_check_agent_llm_optional_model_missing_does_not_stamp_unreachable() -> None:
    """Optional model missing (host reachable) must NOT set reason='unreachable' — status is 'online' in get_status."""
    server, port = _make_ollama_server(["my-reasoning-model"])
    try:
        result = check_agent_llm(CoConfig(
            llm_provider="ollama-openai",
            llm_host=f"http://127.0.0.1:{port}",
            role_models={
                ROLE_REASONING: ModelEntry(model="my-reasoning-model"),
                "coding": ModelEntry(model="missing-coder"),
            },
        ))
        assert result.status == "warn"
        assert result.extra.get("reason") != "unreachable"
    finally:
        server.shutdown()


def test_check_agent_llm_all_models_available_returns_ok() -> None:
    server, port = _make_ollama_server(["my-reasoning-model"])
    try:
        result = check_agent_llm(CoConfig(
            llm_provider="ollama-openai",
            llm_host=f"http://127.0.0.1:{port}",
            role_models={ROLE_REASONING: ModelEntry(model="my-reasoning-model")},
        ))
        assert result.status == "ok"
        assert result.ok
    finally:
        server.shutdown()


def test_check_agent_llm_reasoning_model_missing_returns_error() -> None:
    server, port = _make_ollama_server(["other-model"])
    try:
        result = check_agent_llm(CoConfig(
            llm_provider="ollama-openai",
            llm_host=f"http://127.0.0.1:{port}",
            role_models={ROLE_REASONING: ModelEntry(model="missing-model")},
        ))
        assert result.status == "error"
        assert not result.ok
        assert "missing-model" in result.detail
    finally:
        server.shutdown()


def test_check_agent_llm_optional_role_missing_returns_warn() -> None:
    server, port = _make_ollama_server(["my-reasoning-model"])
    try:
        result = check_agent_llm(CoConfig(
            llm_provider="ollama-openai",
            llm_host=f"http://127.0.0.1:{port}",
            role_models={
                ROLE_REASONING: ModelEntry(model="my-reasoning-model"),
                "coding": ModelEntry(model="missing-coder"),
            },
        ))
        assert result.status == "warn"
        assert result.ok
        assert "coding" in result.detail
    finally:
        server.shutdown()


# --- check_ollama_model ---

def test_check_ollama_model_present_returns_ok() -> None:
    server, port = _make_ollama_server(["target-model"])
    try:
        result = check_ollama_model(f"http://127.0.0.1:{port}", "target-model")
        assert result.status == "ok"
        assert result.ok
    finally:
        server.shutdown()


def test_check_ollama_model_absent_returns_error() -> None:
    server, port = _make_ollama_server(["other-model"])
    try:
        result = check_ollama_model(f"http://127.0.0.1:{port}", "missing-model")
        assert result.status == "error"
        assert not result.ok
        assert "missing-model" in result.detail
    finally:
        server.shutdown()


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
        knowledge_llm_reranker=ModelEntry(provider="gemini", model="gemini-2.0-flash"),
        llm_provider="gemini",
        llm_api_key=None,
    )
    result = check_reranker_llm(config)
    assert result.status == "error"
    assert not result.ok


def test_check_reranker_llm_gemini_with_key_returns_ok() -> None:
    config = CoConfig(
        knowledge_llm_reranker=ModelEntry(provider="gemini", model="gemini-2.0-flash"),
        llm_provider="gemini",
        llm_api_key="test-key",
    )
    result = check_reranker_llm(config)
    assert result.status == "ok"
    assert result.ok


def test_check_reranker_llm_ollama_model_present_returns_ok() -> None:
    server, port = _make_ollama_server(["reranker-model"])
    try:
        config = CoConfig(
            knowledge_llm_reranker=ModelEntry(provider="ollama-openai", model="reranker-model"),
            llm_provider="ollama-openai",
            llm_host=f"http://127.0.0.1:{port}",
        )
        result = check_reranker_llm(config)
        assert result.status == "ok"
        assert result.ok
    finally:
        server.shutdown()


def test_check_reranker_llm_ollama_unreachable_returns_warn() -> None:
    config = CoConfig(
        knowledge_llm_reranker=ModelEntry(provider="ollama-openai", model="reranker-model"),
        llm_provider="ollama-openai",
        llm_host="http://localhost:1",
    )
    result = check_reranker_llm(config)
    # unreachable host → warn (ok=True), caller must degrade because status != "ok"
    assert result.status == "warn"
    assert result.ok


def test_check_reranker_llm_explicit_ollama_provider_overrides_gemini_session() -> None:
    """Explicit provider="ollama-openai" on reranker must probe Ollama, not fall back to Gemini key check."""
    server, port = _make_ollama_server(["reranker-model"])
    try:
        config = CoConfig(
            knowledge_llm_reranker=ModelEntry(provider="ollama-openai", model="reranker-model"),
            llm_provider="gemini",
            llm_api_key="some-key",
            llm_host=f"http://127.0.0.1:{port}",
        )
        result = check_reranker_llm(config)
        # Must probe Ollama, not succeed from the Gemini key — model IS present so result is ok
        assert result.status == "ok"
        assert result.ok
    finally:
        server.shutdown()


def test_check_reranker_llm_ollama_model_absent_returns_error() -> None:
    server, port = _make_ollama_server(["other-model"])
    try:
        config = CoConfig(
            knowledge_llm_reranker=ModelEntry(provider="ollama-openai", model="reranker-model"),
            llm_provider="ollama-openai",
            llm_host=f"http://127.0.0.1:{port}",
        )
        result = check_reranker_llm(config)
        assert result.status == "error"
        assert not result.ok
    finally:
        server.shutdown()


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


def test_check_embedder_ollama_model_present_returns_ok() -> None:
    server, port = _make_ollama_server(["nomic-embed-text"])
    try:
        config = CoConfig(
            knowledge_embedding_provider="ollama",
            knowledge_embedding_model="nomic-embed-text",
            llm_host=f"http://127.0.0.1:{port}",
        )
        result = check_embedder(config)
        assert result.status == "ok"
        assert result.ok
    finally:
        server.shutdown()


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
