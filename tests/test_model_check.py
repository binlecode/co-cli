"""Functional tests for provider and model availability checks."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from co_cli.bootstrap._check import check_llm
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


def test_check_llm_gemini_key_missing_returns_error() -> None:
    result = check_llm(CoConfig(llm_provider="gemini", llm_api_key=None))
    assert result.status == "error"
    assert not result.ok
    assert "LLM_API_KEY" in result.detail


def test_check_llm_gemini_key_present_returns_ok() -> None:
    result = check_llm(CoConfig(llm_provider="gemini", llm_api_key="test-key"))
    assert result.status == "ok"
    assert result.ok


def test_check_llm_ollama_unreachable_returns_warn() -> None:
    # Port 1 is reserved/unreachable — connection refused immediately.
    result = check_llm(CoConfig(llm_provider="ollama-openai", llm_host="http://localhost:1"))
    assert result.status == "warn"
    assert result.ok


def test_check_llm_all_models_available_returns_ok() -> None:
    server, port = _make_ollama_server(["my-reasoning-model"])
    try:
        result = check_llm(CoConfig(
            llm_provider="ollama-openai",
            llm_host=f"http://127.0.0.1:{port}",
            role_models={ROLE_REASONING: ModelEntry(model="my-reasoning-model")},
        ))
        assert result.status == "ok"
        assert result.ok
    finally:
        server.shutdown()


def test_check_llm_reasoning_model_missing_returns_error() -> None:
    server, port = _make_ollama_server(["other-model"])
    try:
        result = check_llm(CoConfig(
            llm_provider="ollama-openai",
            llm_host=f"http://127.0.0.1:{port}",
            role_models={ROLE_REASONING: ModelEntry(model="missing-model")},
        ))
        assert result.status == "error"
        assert not result.ok
        assert "reasoning" in result.detail.lower() or "missing-model" in result.detail
    finally:
        server.shutdown()


def test_check_llm_optional_role_missing_returns_warn() -> None:
    server, port = _make_ollama_server(["my-reasoning-model"])
    try:
        result = check_llm(CoConfig(
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
