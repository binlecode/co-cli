"""Functional tests for error classification functions.

All tests use real or constructed exception objects — no mocks, no stubs.
"""

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError

from co_cli.tools._errors import (
    ToolErrorKind,
    classify_google_error,
    handle_tool_error,
    terminal_error,
)
from co_cli._provider_errors import (
    ProviderErrorAction,
    classify_provider_error,
    _parse_retry_after,
)


# ---------------------------------------------------------------------------
# Helpers — lightweight exception stand-ins with real attributes
# ---------------------------------------------------------------------------


class FakeHttpError(Exception):
    """Mimics googleapiclient.errors.HttpError with resp.status."""

    def __init__(self, status: int, message: str = ""):
        self.resp = type("Resp", (), {"status": str(status)})()
        super().__init__(message or f"<HttpError {status}>")


class FakeHttpErrorWithStatusCode(Exception):
    """Variant using .status_code attribute (some Google client versions)."""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(message or f"<HttpError {status_code}>")


# ---------------------------------------------------------------------------
# classify_google_error
# ---------------------------------------------------------------------------


def test_classify_google_401():
    e = FakeHttpError(401)
    kind, msg = classify_google_error(e)
    assert kind == ToolErrorKind.TERMINAL
    assert "authentication" in msg.lower()


def test_classify_google_403():
    e = FakeHttpError(403)
    kind, msg = classify_google_error(e)
    assert kind == ToolErrorKind.TERMINAL


def test_classify_google_404():
    e = FakeHttpError(404)
    kind, msg = classify_google_error(e)
    assert kind == ToolErrorKind.MISUSE
    assert "not found" in msg.lower()


def test_classify_google_429():
    e = FakeHttpError(429)
    kind, msg = classify_google_error(e)
    assert kind == ToolErrorKind.TRANSIENT
    assert "rate" in msg.lower()


def test_classify_google_500():
    e = FakeHttpError(500)
    kind, msg = classify_google_error(e)
    assert kind == ToolErrorKind.TRANSIENT
    assert "server" in msg.lower()


def test_classify_google_status_code_attribute():
    e = FakeHttpErrorWithStatusCode(429)
    kind, _ = classify_google_error(e)
    assert kind == ToolErrorKind.TRANSIENT


def test_classify_google_not_enabled():
    e = Exception("API has not been enabled for project 12345")
    kind, msg = classify_google_error(e)
    assert kind == ToolErrorKind.TERMINAL
    assert "has not been enabled" in msg


def test_classify_google_access_not_configured():
    e = Exception("accessNotConfigured: some details")
    kind, _ = classify_google_error(e)
    assert kind == ToolErrorKind.TERMINAL


def test_classify_google_unknown_defaults_transient():
    e = Exception("something went wrong")
    kind, _ = classify_google_error(e)
    assert kind == ToolErrorKind.TRANSIENT


# ---------------------------------------------------------------------------
# handle_tool_error
# ---------------------------------------------------------------------------


def test_handle_tool_error_terminal():
    result = handle_tool_error(ToolErrorKind.TERMINAL, "auth failed")
    assert result == terminal_error("auth failed")
    assert result["error"] is True
    assert result["display"] == "auth failed"


def test_handle_tool_error_transient():
    with pytest.raises(ModelRetry, match="rate limited"):
        handle_tool_error(ToolErrorKind.TRANSIENT, "rate limited")


def test_handle_tool_error_misuse():
    with pytest.raises(ModelRetry, match="bad ID"):
        handle_tool_error(ToolErrorKind.MISUSE, "bad ID")


# ---------------------------------------------------------------------------
# classify_provider_error
# ---------------------------------------------------------------------------


def test_classify_provider_400():
    e = ModelHTTPError(400, "test-model", body={"error": "invalid json"})
    action, msg, delay = classify_provider_error(e)
    assert action == ProviderErrorAction.REFLECT
    assert delay == 0.5


def test_classify_provider_401():
    e = ModelHTTPError(401, "test-model", body="Unauthorized")
    action, _, _ = classify_provider_error(e)
    assert action == ProviderErrorAction.ABORT


def test_classify_provider_403():
    e = ModelHTTPError(403, "test-model", body="Forbidden")
    action, _, _ = classify_provider_error(e)
    assert action == ProviderErrorAction.ABORT


def test_classify_provider_404():
    e = ModelHTTPError(404, "test-model", body="Not Found")
    action, _, _ = classify_provider_error(e)
    assert action == ProviderErrorAction.ABORT


def test_classify_provider_429():
    e = ModelHTTPError(429, "test-model", body='{"retry-after": "5"}')
    action, msg, delay = classify_provider_error(e)
    assert action == ProviderErrorAction.BACKOFF_RETRY
    assert "rate" in msg.lower()
    assert delay == 5.0


def test_classify_provider_500():
    e = ModelHTTPError(500, "test-model", body="Internal Server Error")
    action, _, delay = classify_provider_error(e)
    assert action == ProviderErrorAction.BACKOFF_RETRY
    assert delay == 2.0


def test_classify_provider_502():
    e = ModelHTTPError(502, "test-model")
    action, _, _ = classify_provider_error(e)
    assert action == ProviderErrorAction.BACKOFF_RETRY


def test_classify_provider_network_error():
    e = ModelAPIError("test-model", "Connection refused")
    action, msg, delay = classify_provider_error(e)
    assert action == ProviderErrorAction.BACKOFF_RETRY
    assert "Connection refused" in msg
    assert delay == 2.0


# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------


def test_parse_retry_after_none():
    assert _parse_retry_after(None) == 3.0


def test_parse_retry_after_numeric_string():
    assert _parse_retry_after('{"retry-after": "10"}') == 10.0


def test_parse_retry_after_underscore():
    assert _parse_retry_after('{"retry_after": 7}') == 7.0


def test_parse_retry_after_capped_at_60():
    assert _parse_retry_after('{"retry-after": "120"}') == 60.0


def test_parse_retry_after_no_match():
    assert _parse_retry_after("no relevant info") == 3.0
