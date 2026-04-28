"""Tests for the context-overflow HTTP error classifier."""

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from co_cli.context._http_error_classifier import is_context_overflow


def _err(code: int, body: object) -> ModelHTTPError:
    return ModelHTTPError(status_code=code, model_name="test", body=body)


@pytest.mark.parametrize(
    ("code", "body", "expected"),
    [
        (413, "context_length_exceeded: prompt is too long", True),
        (413, None, True),
        (400, {"error": {"message": "maximum context length is 8192"}}, True),
        (400, "prompt is too long for this model", True),
        (400, {"error": {"message": "Request payload size exceeds the limit"}}, True),
        (400, {"error": {"message": "Input token count exceeds the maximum"}}, True),
        (400, {"error": {"code": "context_length_exceeded", "message": ""}}, True),
        (
            400,
            {
                "error": {
                    "message": "Provider returned error",
                    "metadata": {"raw": '{"error": {"message": "prompt is too long"}}'},
                }
            },
            True,
        ),
        (400, {"error": {"message": "invalid JSON in request"}}, False),
        (400, None, False),
        (500, "context_length_exceeded", False),
        (
            400,
            {"error": {"message": "Provider returned error", "metadata": {"raw": "not json"}}},
            False,
        ),
    ],
)
def test_is_context_overflow(code: int, body: object, expected: bool) -> None:
    assert is_context_overflow(_err(code, body)) is expected
