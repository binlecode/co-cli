"""Tests for observability redaction — text helper and JSON log emission path."""

import json
import logging
from pathlib import Path

import pytest

from co_cli.config.observability import _DEFAULT_REDACT_PATTERNS, redact_text
from co_cli.observability import tracing


def _read_log(log: Path) -> list[dict]:
    logger = logging.getLogger("co_cli.observability.spans")
    for h in logger.handlers:
        h.flush()
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


@pytest.fixture
def redact_log(tmp_path: Path):
    """Isolated spans log with redact patterns; restores logger + pattern state on teardown."""
    logger = logging.getLogger("co_cli.observability.spans")
    saved_handlers = list(logger.handlers)
    saved_patterns = list(tracing._COMPILED_PATTERNS)
    for h in saved_handlers:
        logger.removeHandler(h)

    yield tmp_path / "spans.jsonl"

    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    for h in saved_handlers:
        logger.addHandler(h)
    tracing._COMPILED_PATTERNS = saved_patterns


def test_redact_text_removes_credential():
    """A matching credential in free text is replaced with [REDACTED]."""
    text = "summary: used sk-abc12345678901234567890 to call the API"
    result = redact_text(text, _DEFAULT_REDACT_PATTERNS)
    assert "[REDACTED]" in result
    assert "sk-abc" not in result


def test_redact_text_clean_text_unchanged():
    """Text with no credentials passes through unmodified."""
    text = "a clean summary with no credentials"
    assert redact_text(text, _DEFAULT_REDACT_PATTERNS) == text


def test_emission_string_attribute_redacted(redact_log: Path) -> None:
    """A span attribute whose value matches a pattern is [REDACTED] in the emitted JSON record."""
    tracing.setup_log(redact_log, redact_patterns=[r"sk-[A-Za-z0-9]{20,}"])

    @tracing.trace("redact_attr_test")
    def f() -> None:
        tracing.current_span().set_attribute("auth", "token=sk-abc123def456ghi789jkl")

    f()
    recs = _read_log(redact_log)
    assert recs, "expected at least one emitted record"
    assert "sk-abc123def456ghi789jkl" not in recs[0]["attributes"]["auth"]
    assert "[REDACTED]" in recs[0]["attributes"]["auth"]


def test_emission_nested_json_attribute_redacted(redact_log: Path) -> None:
    """Secrets buried 3+ levels deep in a JSON-string attribute are [REDACTED] in the emitted record."""
    tracing.setup_log(redact_log, redact_patterns=[r"sk-[A-Za-z0-9]{20,}"])

    nested = json.dumps(
        [{"role": "user", "parts": [{"type": "text", "content": "key: sk-abc123def456ghi789jkl"}]}]
    )

    @tracing.trace("nested_redact_test")
    def f() -> None:
        tracing.current_span().set_attribute("co.model.input", nested)

    f()
    recs = _read_log(redact_log)
    assert recs, "expected at least one emitted record"
    assert "sk-abc123def456ghi789jkl" not in recs[0]["attributes"]["co.model.input"]
    assert "[REDACTED]" in recs[0]["attributes"]["co.model.input"]
