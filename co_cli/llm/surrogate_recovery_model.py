"""Reactive UnicodeEncodeError recovery — backstop for the proactive sanitizer.

Catches lone surrogates the history-processor whitelist misses, before they
reach json.dumps inside the SDK. Re-sanitizes and retries once.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from co_cli.context.history_processors import sanitize_surrogate_codepoints_messages

log = logging.getLogger(__name__)


class SurrogateRecoveryModel(WrapperModel):
    """Catch ``UnicodeEncodeError``, re-sanitize messages, retry once."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        try:
            return await self.wrapped.request(messages, model_settings, model_request_parameters)
        except UnicodeEncodeError:
            log.warning(
                "Recovered from UnicodeEncodeError via sanitize-retry (request)",
            )
            sanitized = sanitize_surrogate_codepoints_messages(messages)
            return await self.wrapped.request(sanitized, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        opened = False
        try:
            async with self.wrapped.request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as stream:
                opened = True
                yield stream
            return
        except UnicodeEncodeError:
            if opened:
                raise
            log.warning(
                "Recovered from UnicodeEncodeError via sanitize-retry (request_stream)",
            )
        sanitized = sanitize_surrogate_codepoints_messages(messages)
        async with self.wrapped.request_stream(
            sanitized, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream
