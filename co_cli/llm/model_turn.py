"""Graph-free model-turn client over ``direct.model_request_stream``.

``model_turn`` is the single call that replaces the agent graph's model request
for co's owned loop. It drives ``pydantic_ai.direct.model_request_stream``
directly (no ``WrapperModel``, no graph) and applies the three model-boundary
concerns co owns:

1. **Surrogate recovery** — catch ``UnicodeEncodeError`` lone surrogates around
   stream *open*, re-sanitize messages, retry once. A consumer-side error raised
   after the stream opened propagates unchanged (no retry).
2. **``chat`` span** — push a ``kind="model"`` span on entry (``co.model.input``)
   and close it with ``co.model.output`` / ``co.model.tokens.*`` from the
   assembled stream.
3. **JSON arg repair** — when ``repair=True`` (Ollama), repair each string
   ``ToolCallPart.args`` on the assembled ``StreamedResponse.get()`` before
   pydantic validation, via the ``RepairingStreamedResponse`` proxy.

The ``chat`` span is a model-turn-boundary concern this client owns going
forward, so the span-close helpers (``model_span_close_attributes``,
``close_model_span``) live here. The still-live ``SurrogateRecoveryModel``
wrapper imports them in the interim; they may re-home if a third consumer
appears.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai import direct
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.settings import ModelSettings

from co_cli.llm._json_repair import RepairingStreamedResponse
from co_cli.llm._message_sanitize import sanitize_surrogate_codepoints_messages
from co_cli.observability.serialize import serialize_messages, serialize_response
from co_cli.observability.tracing import current_span, pop_span, push_span

log = logging.getLogger(__name__)


def model_span_close_attributes(response: ModelResponse, usage: Any) -> dict[str, Any]:
    """Build the ``chat`` span close attributes shared by the stream and non-stream paths."""
    return {
        "co.model.output": serialize_response(response),
        "co.model.tokens.input": getattr(usage, "input_tokens", 0),
        "co.model.tokens.output": getattr(usage, "output_tokens", 0),
        "co.model.name": response.model_name,
        "co.model.finish_reason": str(response.finish_reason) if response.finish_reason else None,
    }


def close_model_span(stream: Any) -> None:
    """Pop the ``chat`` span, populating close attributes from the assembled stream."""
    try:
        response = stream.get()
        usage = stream.usage()
    except Exception as exc:
        log.debug("model span close: could not read assembled stream: %s", exc)
        pop_span()
        return
    pop_span(attributes=model_span_close_attributes(response, usage))


@asynccontextmanager
async def model_turn(
    model: Model,
    messages: list[ModelMessage],
    model_request_parameters: ModelRequestParameters,
    model_settings: ModelSettings | None,
    *,
    repair: bool,
) -> AsyncIterator[StreamedResponse]:
    """Drive one streamed model turn over ``direct.model_request_stream``.

    ``model`` is the raw provider model (``OpenAIChatModel``/``GoogleModel``), not
    the ``SurrogateRecoveryModel`` wrapper — otherwise the wrapper's concerns would
    double-apply. ``direct.model_request_stream`` re-wraps the model via
    ``instrument_model``, but co keeps pydantic-ai instrumentation off
    (``Agent._instrument_default=False``, no ``instrument_pydantic_ai`` call), so
    it returns the model unwrapped.

    Yields a ``StreamedResponse`` whose ``.get()`` returns the assembled response,
    repaired when ``repair=True``.
    """
    push_span(
        f"chat {model.model_name}",
        kind="model",
        attributes={
            "co.model.name": model.model_name,
            "co.model.input": serialize_messages(messages),
        },
    )
    spanned_stream: Any = None
    try:
        opened = False
        try:
            async with direct.model_request_stream(
                model,
                messages,
                model_settings=model_settings,
                model_request_parameters=model_request_parameters,
            ) as stream:
                opened = True
                spanned_stream = RepairingStreamedResponse(stream) if repair else stream
                yield spanned_stream
        except UnicodeEncodeError:
            if opened:
                raise
            log.warning("Recovered from UnicodeEncodeError via sanitize-retry (model_turn)")
            current_span().add_event("surrogate_recovery", {"method": "model_turn"})
            sanitized = sanitize_surrogate_codepoints_messages(messages)
            async with direct.model_request_stream(
                model,
                sanitized,
                model_settings=model_settings,
                model_request_parameters=model_request_parameters,
            ) as stream:
                spanned_stream = RepairingStreamedResponse(stream) if repair else stream
                yield spanned_stream
    except BaseException as exc:
        pop_span(status="ERROR", status_msg=str(exc))
        raise
    close_model_span(spanned_stream)
