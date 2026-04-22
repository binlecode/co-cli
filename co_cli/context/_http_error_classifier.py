"""Context-overflow HTTP error classifier for the orchestration turn loop."""

import json
import logging

from pydantic_ai.exceptions import ModelHTTPError

logger = logging.getLogger(__name__)

_OVERFLOW_PHRASES = (
    # Preserved from prior co-cli contract
    "prompt is too long",
    "context_length_exceeded",
    "maximum context length",
    # Broader explicit overflow evidence (ported from Hermes reference)
    "context length",
    "context size",
    "context window",
    "token limit",
    "too many tokens",
    "exceeds the limit",
    "input token count",
    "maximum number of tokens",
    "prompt length",
    "input is too long",
    "maximum model length",
    "max input token",
    "exceeds the max_model_len",
    "reduce the length",
)

_OVERFLOW_CODES = frozenset(
    {
        "context_length_exceeded",
        "max_tokens_exceeded",
    }
)


def is_context_overflow(e: ModelHTTPError) -> bool:
    """Return True only when e carries explicit context-overflow evidence.

    HTTP 413 is treated as overflow unconditionally (payload too large by definition).
    HTTP 400 requires explicit body evidence: a recognized overflow phrase in
    error.message, flat message, or wrapped metadata.raw; or a recognized overflow
    error code in error.code. Body parsing failures fall back cleanly to False.
    """
    if e.status_code == 413:
        return True
    if e.status_code != 400:
        return False
    return _body_has_overflow_evidence(e.body)


def _body_has_overflow_evidence(body: object) -> bool:
    if not isinstance(body, dict):
        body_str = str(body).lower() if body is not None else ""
        return any(phrase in body_str for phrase in _OVERFLOW_PHRASES)

    err_obj = body.get("error", {})
    if isinstance(err_obj, dict):
        code = err_obj.get("code")
        if isinstance(code, str) and code.strip().lower() in _OVERFLOW_CODES:
            return True

        msg = err_obj.get("message")
        if isinstance(msg, str) and any(phrase in msg.lower() for phrase in _OVERFLOW_PHRASES):
            return True

        meta = err_obj.get("metadata", {})
        if isinstance(meta, dict):
            raw = meta.get("raw")
            if isinstance(raw, str):
                raw_msg = _extract_raw_message(raw)
                if raw_msg and any(phrase in raw_msg.lower() for phrase in _OVERFLOW_PHRASES):
                    return True

    flat_msg = body.get("message")
    return isinstance(flat_msg, str) and any(
        phrase in flat_msg.lower() for phrase in _OVERFLOW_PHRASES
    )


def _extract_raw_message(raw_json: str) -> str:
    """Parse metadata.raw JSON and return inner error message, or '' on failure."""
    try:
        inner = json.loads(raw_json)
        if not isinstance(inner, dict):
            return ""
        inner_err = inner.get("error", {})
        if isinstance(inner_err, dict):
            msg = inner_err.get("message")
            if isinstance(msg, str):
                return msg
        msg = inner.get("message")
        if isinstance(msg, str):
            return msg
        return ""
    except (json.JSONDecodeError, TypeError):
        return ""
