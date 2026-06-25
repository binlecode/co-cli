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

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from co_cli.llm._json_repair import RepairingStreamedResponse, repair_response
from co_cli.llm._message_sanitize import sanitize_surrogate_codepoints_messages
from co_cli.llm.model_turn import close_model_span, model_span_close_attributes
from co_cli.observability.serialize import serialize_messages
from co_cli.observability.tracing import current_span, pop_span, push_span

log = logging.getLogger(__name__)


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
            response = repair_response(response)
        usage = response.usage
        pop_span(attributes=model_span_close_attributes(response, usage))
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
                        RepairingStreamedResponse(stream) if self.repair_tool_args else stream
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
                        RepairingStreamedResponse(stream) if self.repair_tool_args else stream
                    )
                    yield spanned_stream
        except BaseException as exc:
            pop_span(status="ERROR", status_msg=str(exc))
            raise
        close_model_span(spanned_stream)
