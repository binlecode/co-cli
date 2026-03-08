"""Functional tests for the pre-agent model dependency check gate."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from co_cli._model_check import (
    PreflightResult,
    _check_llm_provider,
    _check_model_availability,
    run_model_check,
)
from co_cli.config import ModelEntry
from co_cli.deps import CoDeps, CoServices, CoConfig
from co_cli.display import TerminalFrontend
from co_cli._shell_backend import ShellBackend


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
        role_models = {
            "reasoning": [
                ModelEntry(model="missing-head"),
                ModelEntry(model="fallback-model"),
            ]
        }
        result = _check_model_availability(
            llm_provider="ollama",
            ollama_host=f"http://127.0.0.1:{port}",
            role_models=role_models,
        )
        assert result.status == "warning"
        assert result.ok
        assert result.role_models is not None
        assert result.role_models["reasoning"] == [ModelEntry(model="fallback-model")]
        # Original role_models must be untouched (pure function)
        assert len(role_models["reasoning"]) == 2
        assert role_models["reasoning"][0].model == "missing-head"
    finally:
        server.shutdown()


def test_check_model_availability_no_reasoning_model_returns_error() -> None:
    server, port = _make_ollama_server(["other-model"])
    try:
        result = _check_model_availability(
            llm_provider="ollama",
            ollama_host=f"http://127.0.0.1:{port}",
            role_models={"reasoning": [ModelEntry(model="missing-model")]},
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
        role_models={"reasoning": [ModelEntry(model="gemini-3-flash-preview")]},
    )
    assert result.status == "ok"
    assert result.ok


# --- run_model_check tests ---


def test_run_model_check_error_result_raises_runtime_error() -> None:
    # Gemini provider with no API key → _check_llm_provider returns error → RuntimeError
    deps = CoDeps(
        services=CoServices(shell=ShellBackend()),
        config=CoConfig(
            llm_provider="gemini",
            gemini_api_key=None,
            role_models={"reasoning": [ModelEntry(model="gemini-3-flash-preview")]},
        ),
    )
    frontend = TerminalFrontend()
    with pytest.raises(RuntimeError):
        run_model_check(deps, frontend)


def test_run_model_check_applies_role_models_mutation_from_check() -> None:
    server, port = _make_ollama_server(["fallback-model"])
    try:
        deps = CoDeps(
            services=CoServices(shell=ShellBackend()),
            config=CoConfig(
                llm_provider="ollama",
                gemini_api_key=None,
                ollama_host=f"http://127.0.0.1:{port}",
                role_models={
                    "reasoning": [
                        ModelEntry(model="missing-head"),
                        ModelEntry(model="fallback-model"),
                    ]
                },
            ),
        )
        frontend = TerminalFrontend()
        run_model_check(deps, frontend)
        # Mutation applied: deps.config.role_models updated to advanced chain
        assert deps.config.role_models["reasoning"] == [ModelEntry(model="fallback-model")]
    finally:
        server.shutdown()
