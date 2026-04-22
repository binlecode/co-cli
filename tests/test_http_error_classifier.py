"""Tests for is_context_overflow() in _http_error_classifier.

Unit coverage for the public predicate and one run_turn() boundary assertion
to verify the helper is wired into orchestration.
"""

import asyncio
import json

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import FunctionModel
from tests._frontend import SilentFrontend
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.context._http_error_classifier import is_context_overflow
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend

_TURN_TIMEOUT_SECS: int = 10


def _err(code: int, body: object) -> ModelHTTPError:
    return ModelHTTPError(status_code=code, model_name="test", body=body)


def _make_deps() -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=make_settings())


# ---------------------------------------------------------------------------
# Unit tests — is_context_overflow() directly
# ---------------------------------------------------------------------------


def test_openai_string_body_overflow() -> None:
    """OpenAI/Ollama string body with overflow phrase returns True."""
    assert is_context_overflow(_err(400, "prompt is too long for this model")) is True


def test_openai_dict_body_nested_message_overflow() -> None:
    """OpenAI dict body with error.message overflow phrase returns True."""
    assert (
        is_context_overflow(_err(400, {"error": {"message": "maximum context length is 8192"}}))
        is True
    )


def test_gemini_exceeds_limit_body_overflow() -> None:
    """Gemini-style 'exceeds the limit' in error.message returns True."""
    assert (
        is_context_overflow(
            _err(400, {"error": {"message": "Request payload size exceeds the limit"}})
        )
        is True
    )


def test_gemini_input_token_count_body_overflow() -> None:
    """Gemini-style 'input token count' in error.message returns True."""
    assert (
        is_context_overflow(
            _err(400, {"error": {"message": "Input token count exceeds the maximum"}})
        )
        is True
    )


def test_structured_error_code_overflow() -> None:
    """Structured body with error.code = 'context_length_exceeded' returns True."""
    assert is_context_overflow(_err(400, {"error": {"code": "context_length_exceeded"}})) is True


def test_structured_error_code_max_tokens_exceeded_overflow() -> None:
    """Structured body with error.code = 'max_tokens_exceeded' returns True."""
    assert is_context_overflow(_err(400, {"error": {"code": "max_tokens_exceeded"}})) is True


def test_flat_body_message_overflow() -> None:
    """Flat top-level 'message' field with overflow phrase returns True."""
    assert is_context_overflow(_err(400, {"message": "Context length exceeded limit"})) is True


def test_wrapped_metadata_raw_overflow() -> None:
    """Wrapped metadata.raw with inner overflow message returns True."""
    raw = json.dumps({"error": {"message": "prompt is too long"}})
    body = {"error": {"message": "Provider returned error", "metadata": {"raw": raw}}}
    assert is_context_overflow(_err(400, body)) is True


def test_malformed_metadata_raw_falls_back_to_not_overflow() -> None:
    """Malformed metadata.raw (invalid JSON) falls back cleanly to False."""
    body = {"error": {"message": "Provider returned error", "metadata": {"raw": "not json {{{"}}}
    assert is_context_overflow(_err(400, body)) is False


def test_generic_tool_call_rejected_not_overflow() -> None:
    """Generic 'invalid JSON arguments' dict body is not overflow."""
    assert (
        is_context_overflow(_err(400, {"error": {"message": "invalid JSON arguments"}})) is False
    )


def test_400_none_body_not_overflow() -> None:
    """HTTP 400 with None body is not overflow."""
    assert is_context_overflow(_err(400, None)) is False


def test_400_empty_string_body_not_overflow() -> None:
    """HTTP 400 with empty string body is not overflow."""
    assert is_context_overflow(_err(400, "")) is False


def test_500_with_overflow_phrase_not_overflow() -> None:
    """HTTP 500 with overflow phrase in body is not overflow (wrong status code)."""
    assert is_context_overflow(_err(500, "context_length_exceeded")) is False


def test_413_no_body_is_overflow() -> None:
    """HTTP 413 with None body is overflow (status code alone is sufficient)."""
    assert is_context_overflow(_err(413, None)) is True


def test_413_with_body_is_overflow() -> None:
    """HTTP 413 with arbitrary body is overflow regardless of body content."""
    assert (
        is_context_overflow(_err(413, {"error": {"message": "request entity too large"}})) is True
    )


# ---------------------------------------------------------------------------
# run_turn() boundary — verifies the helper is wired into orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_overflow_400_wired_into_run_turn() -> None:
    """Gemini-style overflow 400 is routed to overflow path, not reformulation, via run_turn().

    Verifies that is_context_overflow() is actually called by the orchestrator:
    the Gemini body was unrecognized by the old predicate, so reaching the
    overflow status message proves the new classifier is in the call path.
    """

    async def _raise_gemini_overflow(messages, agent_info):
        raise ModelHTTPError(
            status_code=400,
            model_name="test",
            body={"error": {"message": "Input token count exceeds the maximum"}},
        )
        yield  # makes this an async generator

    deps = _make_deps()
    agent = build_agent(
        config=deps.config, model=FunctionModel(stream_function=_raise_gemini_overflow)
    )
    frontend = SilentFrontend()

    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert turn.outcome == "error"
    assert any("Context overflow" in s for s in frontend.statuses)
    assert not any("Tool call rejected" in s for s in frontend.statuses)
