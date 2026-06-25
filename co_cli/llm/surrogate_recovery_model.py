"""WrapperModel hosting co's model-path cross-cutting behaviors.

Three concerns, all at the model-request boundary co already owns:

1. **Surrogate recovery** — catch ``UnicodeEncodeError`` lone surrogates the
   history-processor whitelist misses, re-sanitize messages, retry once.
2. **``chat`` span** — push a ``kind="model"`` span on entry (``co.model.input``)
   and close it with ``co.model.output`` / ``co.model.tokens.*`` once the response
   (non-stream) or assembled stream (streaming) is available.
3. **JSON arg repair** — gated to the Ollama-backed model: apply syntactic repair
   to each ``ToolCallPart.args`` on the returned ``ModelResponse`` BEFORE pydantic
   validation. On the streaming path the graph validates ``StreamedResponse.get()``,
   so a thin proxy repairs that assembled response; non-stream repairs the response
   directly. Idempotent on valid JSON (gating is cleanliness, not correctness).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from co_cli.llm._message_sanitize import sanitize_surrogate_codepoints_messages
from co_cli.observability.serialize import serialize_messages, serialize_response
from co_cli.observability.tracing import current_span, pop_span, push_span

log = logging.getLogger(__name__)

_CLOSE_FOR: dict[str, str] = {"{": "}", "[": "]"}
_OPEN_FOR: dict[str, str] = {v: k for k, v in _CLOSE_FOR.items()}
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_JSON_REPAIR_MAX_TRIM_STEPS = 50
"""Max trailing-delimiter trim passes in JSON arg repair (bounds the trim loop)."""


def _try_parse(s: str) -> str | None:
    try:
        return json.dumps(json.loads(s, strict=False))
    except json.JSONDecodeError:
        return None


def _balance_brackets(s: str) -> str:
    """Append missing closing brackets by tracking the open-bracket stack."""
    stack: list[str] = []
    in_str = False
    escape_next = False
    for ch in s:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_str:
            escape_next = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in _CLOSE_FOR:
            stack.append(ch)
        elif ch in _OPEN_FOR and stack and stack[-1] == _OPEN_FOR[ch]:
            stack.pop()
    return s + "".join(_CLOSE_FOR[o] for o in reversed(stack))


def _repair_json_args(raw: str) -> str:
    """Apply syntactic repair passes to a malformed tool-call arguments string.

    Purely syntactic — no value inference or schema awareness. Returns a valid
    JSON string on success, or '{}' if all passes fail (so pydantic validation
    can raise ModelRetry rather than crashing the session).
    """
    if not raw or not raw.strip():
        return "{}"
    s = raw.strip()

    if s == "None":
        return "{}"

    # Control-char escape — strict=False accepts literal tabs/newlines;
    # re-serialise to produce a spec-compliant string.
    result = _try_parse(s)
    if result is not None:
        return result

    # Trailing-comma strip (common in quantized-model output)
    s = _TRAILING_COMMA.sub(r"\1", s)
    result = _try_parse(s)
    if result is not None:
        return result

    # Balance unclosed brackets, then re-strip trailing commas that now
    # precede the appended closer.
    s = _TRAILING_COMMA.sub(r"\1", _balance_brackets(s))
    result = _try_parse(s)
    if result is not None:
        return result

    # Trim excess trailing closing delimiters (bounded — see _JSON_REPAIR_MAX_TRIM_STEPS).
    for _ in range(_JSON_REPAIR_MAX_TRIM_STEPS):
        s = s.rstrip()
        if not s or s[-1] not in ("}", "]"):
            break
        s = s[:-1]
        result = _try_parse(s)
        if result is not None:
            return result

    return "{}"


def _repair_response(response: ModelResponse) -> ModelResponse:
    """Return a copy of ``response`` with each string ``ToolCallPart.args`` repaired.

    Repair is idempotent on already-valid JSON, so unchanged parts are preserved
    by identity and the response is rebuilt only when something actually changed.
    """
    new_parts = []
    changed = False
    for part in response.parts:
        if isinstance(part, ToolCallPart) and isinstance(part.args, str):
            repaired = _repair_json_args(part.args)
            if repaired != part.args:
                new_parts.append(replace(part, args=repaired))
                changed = True
                continue
        new_parts.append(part)
    if not changed:
        return response
    return replace(response, parts=new_parts)


class _RepairingStreamedResponse:
    """StreamedResponse proxy that repairs tool-call args on the assembled ``get()``.

    Explicit read-surface contract — what the agent graph actually touches:
    - ``get()`` — the graph validates and dispatches tools from the assembled
      ``StreamedResponse.get()`` (``_agent_graph.py`` ``_streaming_handler``, :637),
      so repairing here lands the fix before pydantic validation.
    - ``__aiter__`` / ``usage()`` — delegate to the wrapped stream verbatim.

    ``__getattr__`` stays as the catch-all for every other member the graph or
    instrumentation reads (``model_name``, ``timestamp``, ``provider_*``,
    ``close_stream``, and the stream passed to ``_build_agent_stream``). It is
    deliberately NOT replaced by an enumerated member list — the SDK reads the
    streamed response beyond the hot members above, so enumerate-and-drop would
    ``AttributeError`` on any unlisted access. Subclassing ``StreamedResponse`` is
    not viable: its ``get()``/``usage()`` read ``self._parts_manager``/``self._usage``,
    which are populated from raw provider chunks the inner stream has already
    consumed — a subclass could fill them only by privately coupling to the inner.
    """

    def __init__(self, inner: StreamedResponse) -> None:
        self._inner = inner
        self._cached: ModelResponse | None = None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._inner.__aiter__()

    def get(self) -> ModelResponse:
        if self._cached is None:
            self._cached = _repair_response(self._inner.get())
        return self._cached

    def usage(self) -> Any:
        return self._inner.usage()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _model_span_close_attributes(response: ModelResponse, usage: Any) -> dict[str, Any]:
    """Build the ``chat`` span close attributes shared by the stream and non-stream paths."""
    return {
        "co.model.output": serialize_response(response),
        "co.model.tokens.input": getattr(usage, "input_tokens", 0),
        "co.model.tokens.output": getattr(usage, "output_tokens", 0),
        "co.model.name": response.model_name,
        "co.model.finish_reason": str(response.finish_reason) if response.finish_reason else None,
    }


def _close_model_span(stream: Any) -> None:
    """Pop the ``chat`` span, populating close attributes from the assembled stream."""
    try:
        response = stream.get()
        usage = stream.usage()
    except Exception as exc:
        log.debug("model span close: could not read assembled stream: %s", exc)
        pop_span()
        return
    pop_span(attributes=_model_span_close_attributes(response, usage))


class SurrogateRecoveryModel(WrapperModel):
    """Wrap a model with surrogate recovery, the ``chat`` span, and gated arg repair."""

    def __init__(self, wrapped: Any, *, repair_tool_args: bool = False) -> None:
        super().__init__(wrapped)
        self.repair_tool_args = repair_tool_args

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        push_span(
            f"chat {self.model_name}",
            kind="model",
            attributes={
                "co.model.name": self.model_name,
                "co.model.input": serialize_messages(messages),
            },
        )
        try:
            try:
                response = await self.wrapped.request(
                    messages, model_settings, model_request_parameters
                )
            except UnicodeEncodeError:
                log.warning("Recovered from UnicodeEncodeError via sanitize-retry (request)")
                current_span().add_event("surrogate_recovery", {"method": "request"})
                sanitized = sanitize_surrogate_codepoints_messages(messages)
                response = await self.wrapped.request(
                    sanitized, model_settings, model_request_parameters
                )
        except BaseException as exc:
            pop_span(status="ERROR", status_msg=str(exc))
            raise
        if self.repair_tool_args:
            response = _repair_response(response)
        usage = response.usage
        pop_span(attributes=_model_span_close_attributes(response, usage))
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        push_span(
            f"chat {self.model_name}",
            kind="model",
            attributes={
                "co.model.name": self.model_name,
                "co.model.input": serialize_messages(messages),
            },
        )
        spanned_stream: Any = None
        try:
            opened = False
            try:
                async with self.wrapped.request_stream(
                    messages, model_settings, model_request_parameters, run_context
                ) as stream:
                    opened = True
                    spanned_stream = (
                        _RepairingStreamedResponse(stream) if self.repair_tool_args else stream
                    )
                    yield spanned_stream
            except UnicodeEncodeError:
                if opened:
                    raise
                log.warning(
                    "Recovered from UnicodeEncodeError via sanitize-retry (request_stream)"
                )
                current_span().add_event("surrogate_recovery", {"method": "request_stream"})
                sanitized = sanitize_surrogate_codepoints_messages(messages)
                async with self.wrapped.request_stream(
                    sanitized, model_settings, model_request_parameters, run_context
                ) as stream:
                    spanned_stream = (
                        _RepairingStreamedResponse(stream) if self.repair_tool_args else stream
                    )
                    yield spanned_stream
        except BaseException as exc:
            pop_span(status="ERROR", status_msg=str(exc))
            raise
        _close_model_span(spanned_stream)
