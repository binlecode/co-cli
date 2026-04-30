"""Tests for the HTTP context-overflow error classifier."""

import json

from pydantic_ai.exceptions import ModelHTTPError

from co_cli.context._http_error_classifier import is_context_overflow


def _err(status_code: int, body: object) -> ModelHTTPError:
    return ModelHTTPError(status_code=status_code, model_name="test-model", body=body)


def test_413_unconditional_overflow():
    """HTTP 413 must classify as overflow regardless of body content."""
    assert is_context_overflow(_err(413, {})) is True
    assert is_context_overflow(_err(413, None)) is True
    assert is_context_overflow(_err(413, "unrelated body text")) is True


def test_500_not_overflow():
    """Non-413/400 status codes must not classify as overflow even with overflow phrases."""
    assert is_context_overflow(_err(500, {"error": {"message": "prompt is too long"}})) is False
    assert is_context_overflow(_err(503, {})) is False


def test_400_overflow_phrase_in_error_message():
    """HTTP 400 with a recognized overflow phrase in error.message must classify as overflow."""
    body = {"error": {"message": "prompt is too long for this model"}}
    assert is_context_overflow(_err(400, body)) is True

    body2 = {"error": {"message": "context window exceeded maximum token limit"}}
    assert is_context_overflow(_err(400, body2)) is True


def test_400_recognized_overflow_error_code():
    """HTTP 400 with a recognized overflow error code must classify as overflow."""
    body = {"error": {"code": "context_length_exceeded", "message": "unrelated text"}}
    assert is_context_overflow(_err(400, body)) is True

    body2 = {"error": {"code": "max_tokens_exceeded"}}
    assert is_context_overflow(_err(400, body2)) is True


def test_400_overflow_phrase_nested_in_metadata_raw():
    """HTTP 400 with overflow phrase inside metadata.raw JSON must classify as overflow."""
    raw_inner = json.dumps({"error": {"message": "context window exceeded the maximum"}})
    body = {"error": {"metadata": {"raw": raw_inner}}}
    assert is_context_overflow(_err(400, body)) is True


def test_400_overflow_in_flat_message_field():
    """HTTP 400 with overflow phrase in the top-level message field must classify as overflow."""
    body = {"message": "input token count exceeds the limit for this model"}
    assert is_context_overflow(_err(400, body)) is True


def test_400_no_overflow_evidence_returns_false():
    """HTTP 400 with no overflow evidence must not classify as overflow."""
    body = {"error": {"message": "invalid api key", "code": "authentication_failed"}}
    assert is_context_overflow(_err(400, body)) is False

    body2 = {"error": {"message": "rate limit exceeded"}}
    assert is_context_overflow(_err(400, body2)) is False


def test_400_non_dict_body_with_overflow_phrase():
    """HTTP 400 with a non-dict body string containing an overflow phrase must classify."""
    assert is_context_overflow(_err(400, "context length exceeded")) is True


def test_400_non_dict_body_without_overflow():
    """HTTP 400 with a non-dict body string with no overflow phrase must not classify."""
    assert is_context_overflow(_err(400, "bad request")) is False
    assert is_context_overflow(_err(400, None)) is False
