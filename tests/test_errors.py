"""Functional tests for provider error classification.

Tests exercise real classify_provider_error() — the logic that drives
retry vs abort decisions in the orchestration layer.
"""

from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError

from co_cli._provider_errors import ProviderErrorAction, classify_provider_error, _parse_retry_after


def test_classify_400_reflects():
    """400 (bad request) → REFLECT to let model fix its tool call."""
    e = ModelHTTPError(400, "test-model", body={"error": "invalid json"})
    action, msg, delay = classify_provider_error(e)
    assert action == ProviderErrorAction.REFLECT
    assert delay == 0.5


def test_classify_401_aborts():
    """401 (unauthorized) → ABORT, no retry."""
    e = ModelHTTPError(401, "test-model", body="Unauthorized")
    action, _, _ = classify_provider_error(e)
    assert action == ProviderErrorAction.ABORT


def test_classify_429_backs_off():
    """429 (rate limit) → BACKOFF_RETRY with parsed delay."""
    e = ModelHTTPError(429, "test-model", body='{"retry-after": "5"}')
    action, msg, delay = classify_provider_error(e)
    assert action == ProviderErrorAction.BACKOFF_RETRY
    assert "rate" in msg.lower()
    assert delay == 5.0


def test_classify_500_backs_off():
    """500 (server error) → BACKOFF_RETRY."""
    e = ModelHTTPError(500, "test-model", body="Internal Server Error")
    action, _, delay = classify_provider_error(e)
    assert action == ProviderErrorAction.BACKOFF_RETRY
    assert delay == 2.0


def test_classify_network_error_backs_off():
    """Network error → BACKOFF_RETRY."""
    e = ModelAPIError("test-model", "Connection refused")
    action, msg, delay = classify_provider_error(e)
    assert action == ProviderErrorAction.BACKOFF_RETRY
    assert "Connection refused" in msg


def test_parse_retry_after_from_dict_body():
    """_parse_retry_after returns default 3.0 when body is a Python dict.

    Bug: str({"retry_after": 10}) produces "{'retry_after': 10}" with single
    quotes. The regex ["\\s:]+ only matches double-quote, whitespace, and colon
    chars — single quote is not in the set, so the match fails immediately after
    the key name and the server-specified delay is silently discarded.

    A 429 response with a structured body (common in real LLM APIs) therefore
    always retries after 3.0s instead of respecting the server's requested delay.
    """
    delay = _parse_retry_after({"retry_after": 10})
    assert delay == 10.0, (
        f"Expected delay=10.0 from dict body, got {delay}. "
        "str() of a Python dict uses single quotes; the regex only matches "
        "double-quote chars, so the value is silently dropped."
    )
