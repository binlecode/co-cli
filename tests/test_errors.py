"""Functional tests for error helpers.

All tests use real or constructed exception objects — no mocks, no stubs.
"""

from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError

from co_cli.tools._errors import terminal_error, http_status_code
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
# http_status_code
# ---------------------------------------------------------------------------


def test_http_status_from_resp_status_int_like_string():
    assert http_status_code(FakeHttpError(401)) == 401


def test_http_status_from_status_code_attribute():
    assert http_status_code(FakeHttpErrorWithStatusCode(429)) == 429


def test_http_status_none_without_status_fields():
    assert http_status_code(Exception("no http status")) is None


def test_http_status_none_for_non_numeric_resp_status():
    e = Exception("bad status")
    e.resp = type("Resp", (), {"status": "not-a-number"})()  # type: ignore[attr-defined]
    assert http_status_code(e) is None


# ---------------------------------------------------------------------------
# terminal_error
# ---------------------------------------------------------------------------


def test_terminal_error_structure():
    result = terminal_error("auth failed")
    assert result == {"display": "auth failed", "error": True}


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
