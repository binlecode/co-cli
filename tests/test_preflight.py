"""Functional tests for the pre-agent preflight resource gate."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from co_cli._preflight import (
    PreflightResult,
    _check_llm_provider,
    _check_model_availability,
    run_preflight,
)
from co_cli.deps import CoDeps
from co_cli.display import TerminalFrontend
from co_cli.shell_backend import ShellBackend


# --- _check_llm_provider tests ---


def test_check_llm_provider_gemini_key_missing_returns_error() -> None:
    result = _check_llm_provider(
        llm_provider="gemini",
        gemini_api_key=None,
        ollama_host="http://localhost:11434",
    )
    assert result.status == "error"
    assert not result.ok
    assert "GEMINI_API_KEY" in result.message


def test_check_llm_provider_non_gemini_key_missing_returns_warning() -> None:
    # Non-Gemini provider with a reachable server (we need to avoid triggering the
    # Ollama reachability check). Use a non-"ollama" provider string.
    result = _check_llm_provider(
        llm_provider="custom",
        gemini_api_key=None,
        ollama_host="http://localhost:11434",
    )
    assert result.status == "warning"
    assert result.ok
    assert "Gemini" in result.message


def test_check_llm_provider_ollama_unreachable_returns_warning() -> None:
    # Port 1 is reserved/unreachable — connection refused immediately.
    result = _check_llm_provider(
        llm_provider="ollama",
        gemini_api_key=None,
        ollama_host="http://localhost:1",
    )
    assert result.status == "warning"
    assert result.ok


# --- _check_model_availability tests ---


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


def test_check_model_availability_chain_advanced_returns_warning_with_updated_roles() -> None:
    server, port = _make_ollama_server(["fallback-model"])
    try:
        model_roles = {"reasoning": ["missing-head", "fallback-model"]}
        result = _check_model_availability(
            llm_provider="ollama",
            ollama_host=f"http://127.0.0.1:{port}",
            model_roles=model_roles,
        )
        assert result.status == "warning"
        assert result.ok
        assert result.model_roles is not None
        assert result.model_roles["reasoning"] == ["fallback-model"]
        # Original model_roles must be untouched (pure function)
        assert model_roles["reasoning"] == ["missing-head", "fallback-model"]
    finally:
        server.shutdown()


def test_check_model_availability_no_reasoning_model_returns_error() -> None:
    server, port = _make_ollama_server(["other-model"])
    try:
        result = _check_model_availability(
            llm_provider="ollama",
            ollama_host=f"http://127.0.0.1:{port}",
            model_roles={"reasoning": ["missing-model"]},
        )
        assert result.status == "error"
        assert not result.ok
        assert "reasoning" in result.message.lower()
    finally:
        server.shutdown()


def test_check_model_availability_non_ollama_returns_ok() -> None:
    result = _check_model_availability(
        llm_provider="gemini",
        ollama_host="http://localhost:11434",
        model_roles={"reasoning": ["gemini-3-flash-preview"]},
    )
    assert result.status == "ok"
    assert result.ok


# --- run_preflight tests ---


def test_run_preflight_error_result_raises_runtime_error() -> None:
    # Gemini provider with no API key → _check_llm_provider returns error → RuntimeError
    deps = CoDeps(
        shell=ShellBackend(),
        llm_provider="gemini",
        gemini_api_key=None,
        model_roles={"reasoning": ["gemini-3-flash-preview"]},
    )
    frontend = TerminalFrontend()
    with pytest.raises(RuntimeError):
        run_preflight(deps, frontend)


def test_run_preflight_applies_model_roles_mutation_from_check() -> None:
    server, port = _make_ollama_server(["fallback-model"])
    try:
        deps = CoDeps(
            shell=ShellBackend(),
            llm_provider="ollama",
            gemini_api_key=None,
            ollama_host=f"http://127.0.0.1:{port}",
            model_roles={"reasoning": ["missing-head", "fallback-model"]},
        )
        frontend = TerminalFrontend()
        run_preflight(deps, frontend)
        # Mutation applied: deps.model_roles updated to advanced chain
        assert deps.model_roles["reasoning"] == ["fallback-model"]
    finally:
        server.shutdown()
