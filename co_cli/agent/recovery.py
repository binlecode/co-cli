"""Typed provider-error classification + length-retry decision for the owned loop.

The owned (graph-free) turn loop's recovery seam — the single home for
``_transient_error_message`` / ``_handle_model_http_error`` / ``_length_retry_settings``
style provider-error handling, owned entirely by the loop.

``classify_provider_error`` turns a raised provider exception into a typed
``ErrorClass`` — an action (recover-overflow / reflect-400 / terminal), the
``TurnExit`` reason, the user-facing status message (ported **verbatim** from the
graph wording, branch-for-branch — CD-m-5), and the turn-span event. The loop reads
``err.action`` and never string-matches exception text.

``length_retry_settings`` is the ``ModelResponse``-shaped port of the graph's
``_length_retry_settings``: same finish-reason + text-presence + ceiling gate, same
boost ladder (doublings 4096 → 8192 → 16384), same Ollama ``cap_output_tokens``
lockstep. Constants live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

import httpx
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import TextPart

from co_cli.agent.turn_state import TurnExit
from co_cli.config.llm import cap_output_tokens
from co_cli.context.compaction import is_context_overflow

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.settings import ModelSettings

_LENGTH_RETRY_CEILING = 16_384
"""Max output tokens ceiling for length-continuation auto-retry. Doublings from 4096 → 8192 → 16384."""

_LENGTH_RETRY_BOOST = 2
"""Multiplier applied to max_tokens on each length-continuation retry."""

assert _LENGTH_RETRY_BOOST > 1, "boost must strictly increase max_tokens for retry to terminate"

_HTTP_400_REFLECT_BACKOFF_SECS = 0.5
"""Backoff before reflecting an HTTP 400 tool-call rejection back to the model."""

_TIMEOUT_MESSAGE = (
    "LLM call timed out — model did not respond in time."
    " Try a shorter prompt, or ask Co 'what can you do right now?' or run /doctor."
)
"""Verbatim graph wording (``_transient_error_message`` TimeoutError branch)."""


class ErrorAction(Enum):
    """What the loop should do with a classified provider error."""

    RECOVER_OVERFLOW = auto()
    """Context overflow → strip-then-summarize, retry once (latched per turn)."""

    REFLECT_400 = auto()
    """HTTP 400 tool-call rejection → reflect to the model, within a per-turn budget."""

    TERMINAL = auto()
    """Transient / timeout / malformed / other HTTP → end the turn (no inner retry, D1)."""


@dataclass(frozen=True)
class ErrorClass:
    """Typed classification of a raised provider error.

    ``message`` is the user-facing status (graph-parity wording) emitted when the
    error resolves terminal — for ``RECOVER_OVERFLOW`` / ``REFLECT_400`` it is the
    fall-through message used only when recovery/budget is exhausted. ``span_event``
    is the ``(name, attrs)`` added to the turn span on the terminal path.
    """

    action: ErrorAction
    exit_reason: TurnExit
    message: str
    span_event: tuple[str, dict]


def classify_provider_error(exc: Exception) -> ErrorClass:
    """Classify a provider exception into a typed ``ErrorClass`` (graph-parity wording).

    ``ModelHTTPError`` is checked before ``ModelAPIError`` (its superclass): a context
    overflow routes to ``RECOVER_OVERFLOW``, a 400 to ``REFLECT_400``, any other HTTP
    code to ``TERMINAL`` with the ``Provider error (HTTP {code})`` status. ``TimeoutError``
    yields the timeout text + ``TIMEOUT`` exit; other ``ModelAPIError`` / ``httpx.ReadError``
    the ``Network error:`` form; ``UnexpectedModelBehavior`` the malformed-output form.
    """
    if isinstance(exc, ModelHTTPError):
        code = exc.status_code
        http_event = (
            "provider_error",
            {"http.status_code": code, "error.body": str(exc.body)[:500]},
        )
        provider_status = f"Provider error (HTTP {code}): {exc.body}"
        if is_context_overflow(exc):
            return ErrorClass(
                action=ErrorAction.RECOVER_OVERFLOW,
                exit_reason=TurnExit.PROVIDER_ERROR,
                message="Context overflow — unrecoverable.",
                span_event=http_event,
            )
        if code == 400:
            return ErrorClass(
                action=ErrorAction.REFLECT_400,
                exit_reason=TurnExit.PROVIDER_ERROR,
                message=provider_status,
                span_event=http_event,
            )
        return ErrorClass(
            action=ErrorAction.TERMINAL,
            exit_reason=TurnExit.PROVIDER_ERROR,
            message=provider_status,
            span_event=http_event,
        )
    if isinstance(exc, TimeoutError):
        return ErrorClass(
            action=ErrorAction.TERMINAL,
            exit_reason=TurnExit.TIMEOUT,
            message=_TIMEOUT_MESSAGE,
            span_event=(
                "transient_error",
                {"error.type": type(exc).__name__, "error.msg": str(exc)[:500]},
            ),
        )
    if isinstance(exc, ModelAPIError | httpx.ReadError):
        return ErrorClass(
            action=ErrorAction.TERMINAL,
            exit_reason=TurnExit.PROVIDER_ERROR,
            message=f"Network error: {exc}",
            span_event=(
                "transient_error",
                {"error.type": type(exc).__name__, "error.msg": str(exc)[:500]},
            ),
        )
    if isinstance(exc, UnexpectedModelBehavior):
        return ErrorClass(
            action=ErrorAction.TERMINAL,
            exit_reason=TurnExit.PROVIDER_ERROR,
            message=f"Model returned malformed output: {exc}",
            span_event=(
                "malformed_output",
                {"error.type": type(exc).__name__, "error.msg": str(exc)[:500]},
            ),
        )
    return ErrorClass(
        action=ErrorAction.TERMINAL,
        exit_reason=TurnExit.PROVIDER_ERROR,
        message=f"Provider error — turn ended: {exc}",
        span_event=("provider_error", {"error.type": type(exc).__name__}),
    )


def length_retry_settings(
    response: ModelResponse,
    active_settings: ModelSettings | None,
) -> ModelSettings | None:
    """Return boosted ``ModelSettings`` if a length-continuation retry should fire, else None.

    ``ModelResponse``-shaped port of the graph's ``_length_retry_settings`` (which reads
    only ``result.response``). Fires when finish_reason is ``'length'``, ``active_settings``
    carries a ``max_tokens`` below ``_LENGTH_RETRY_CEILING``, and the response has at least
    one ``TextPart`` (the text-presence gate — a truncated ``ToolCallPart`` would carry
    malformed JSON into an unanswered tool_calls entry the provider rejects; those fall
    through to the post-turn ceiling diagnostics instead). Returns settings with
    ``max_tokens`` doubled (capped at the ceiling), via ``cap_output_tokens`` (Ollama
    scalar + ``extra_body`` lockstep), or None when no retry should fire.
    """
    if response.finish_reason != "length":
        return None
    current_max = active_settings.get("max_tokens", 0) if active_settings else 0
    if not current_max or current_max >= _LENGTH_RETRY_CEILING:
        return None
    if not any(isinstance(p, TextPart) for p in response.parts):
        return None
    boosted = min(current_max * _LENGTH_RETRY_BOOST, _LENGTH_RETRY_CEILING)
    return cap_output_tokens(active_settings, boosted)
