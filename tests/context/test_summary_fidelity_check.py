"""Pure, no-LLM tests for the summarizer fidelity check — does the deterministic
identifier extract + survival decision flag a summary that silently dropped a
high-signal value (file path, error string)?"""

from co_cli.context.summarization import (
    _extract_identifiers,
    _retry_warranted,
)

# A source carrying the three identifier classes the backstop must catch: a
# path:line ref, an UNQUOTED error string, and a quoted user correction.
_SOURCE = (
    "user: The token check in co_cli/auth.py:42 is inverted.\n"
    "tool_result: Traceback ... raised InvalidSignatureError on decode.\n"
    "user: 'use Argon2 not bcrypt'\n"
)


def test_dropping_path_and_error_warrants_retry():
    """A summary that keeps only the correction but drops the path and error string
    misses a majority of identifiers — over threshold, retry needed."""
    required = _extract_identifiers(_SOURCE)
    summary = "## User Corrections\nuse Argon2 not bcrypt"
    assert _retry_warranted(required, summary) is True


def test_keeping_all_identifiers_does_not_warrant_retry():
    """A summary that preserves every extracted identifier verbatim is fully faithful
    — nothing missing, no retry."""
    required = _extract_identifiers(_SOURCE)
    summary = (
        "## Critical Context\nco_cli/auth.py:42 raised InvalidSignatureError "
        "(Traceback).\n## User Corrections\nuse Argon2 not bcrypt\n"
    )
    assert _retry_warranted(required, summary) is False


def test_zero_identifier_source_never_warrants_retry():
    """A transcript with no extractable high-signal identifiers short-circuits —
    the retry must never fire on identifier-free conversations."""
    source = "user: please make the wording nicer and warmer overall"
    required = _extract_identifiers(source)
    assert required == set()
    assert _retry_warranted(required, "## Goal\nimprove the tone") is False
