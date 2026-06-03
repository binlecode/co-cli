"""Tests for SurrogateRecoveryModel — reactive UnicodeEncodeError backstop."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from co_cli.llm.surrogate_recovery_model import SurrogateRecoveryModel
from co_cli.observability import tracing


class _FakeStream(StreamedResponse):
    """Minimal StreamedResponse with no events — for context-manager testing only."""

    def __init__(self, mrp: ModelRequestParameters):
        super().__init__(mrp)

    async def _get_event_iterator(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def provider_name(self) -> str | None:
        return "fake"

    @property
    def provider_url(self) -> str | None:
        return None

    @property
    def timestamp(self) -> datetime:
        return datetime.now(UTC)


class _FakeModel(Model):
    """Fake model that records calls and can be programmed to raise on Nth call."""

    def __init__(self, raise_n_times: int = 0):
        super().__init__()
        self.raise_n_times = raise_n_times
        self.request_calls: list[list[ModelMessage]] = []
        self.stream_calls: list[list[ModelMessage]] = []

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def system(self) -> str:
        return "fake"

    def _maybe_raise(self) -> None:
        if len(self.request_calls) + len(self.stream_calls) <= self.raise_n_times:
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self.request_calls.append(messages)
        self._maybe_raise()
        return ModelResponse(parts=[TextPart(content="ok")])

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[StreamedResponse]:
        self.stream_calls.append(messages)
        self._maybe_raise()
        yield _FakeStream(model_request_parameters)

    async def count_tokens(self, *args: Any, **kwargs: Any) -> RequestUsage:
        return RequestUsage()


def _mrp() -> ModelRequestParameters:
    return ModelRequestParameters()


def _surrogate_msg() -> list[ModelMessage]:
    return [ModelRequest(parts=[UserPromptPart(content="hello\ud800world")])]


def _clean_msg() -> list[ModelMessage]:
    return [ModelRequest(parts=[UserPromptPart(content="hello world")])]


@pytest.mark.asyncio
async def test_request_passes_through_clean():
    """Clean request reaches wrapped on first try — no retry."""
    fake = _FakeModel(raise_n_times=0)
    wrapper = SurrogateRecoveryModel(fake)
    response = await wrapper.request(_clean_msg(), None, _mrp())
    assert isinstance(response, ModelResponse)
    assert len(fake.request_calls) == 1


@pytest.mark.asyncio
async def test_request_recovers_unicode_error():
    """UnicodeEncodeError on first call triggers sanitize-retry, succeeds on second."""
    fake = _FakeModel(raise_n_times=1)
    wrapper = SurrogateRecoveryModel(fake)
    response = await wrapper.request(_surrogate_msg(), None, _mrp())
    assert isinstance(response, ModelResponse)
    assert len(fake.request_calls) == 2
    retry_msg = fake.request_calls[1][0]
    assert isinstance(retry_msg, ModelRequest)
    assert retry_msg.parts[0].content == "hello�world"


@pytest.mark.asyncio
async def test_request_propagates_after_retry_fails():
    """If both attempts raise UnicodeEncodeError, propagate to caller."""
    fake = _FakeModel(raise_n_times=2)
    wrapper = SurrogateRecoveryModel(fake)
    with pytest.raises(UnicodeEncodeError):
        await wrapper.request(_surrogate_msg(), None, _mrp())
    assert len(fake.request_calls) == 2


@pytest.mark.asyncio
async def test_request_stream_passes_through_clean():
    """Clean streaming request reaches wrapped on first try — no retry."""
    fake = _FakeModel(raise_n_times=0)
    wrapper = SurrogateRecoveryModel(fake)
    async with wrapper.request_stream(_clean_msg(), None, _mrp()) as stream:
        assert isinstance(stream, StreamedResponse)
    assert len(fake.stream_calls) == 1


@pytest.mark.asyncio
async def test_request_stream_recovers_unicode_error():
    """UnicodeEncodeError during stream open triggers sanitize-retry."""
    fake = _FakeModel(raise_n_times=1)
    wrapper = SurrogateRecoveryModel(fake)
    async with wrapper.request_stream(_surrogate_msg(), None, _mrp()) as stream:
        assert isinstance(stream, StreamedResponse)
    assert len(fake.stream_calls) == 2
    retry_msg = fake.stream_calls[1][0]
    assert isinstance(retry_msg, ModelRequest)
    assert retry_msg.parts[0].content == "hello�world"


@pytest.mark.asyncio
async def test_request_stream_propagates_after_retry_fails():
    """If both stream attempts raise UnicodeEncodeError, propagate."""
    fake = _FakeModel(raise_n_times=2)
    wrapper = SurrogateRecoveryModel(fake)
    with pytest.raises(UnicodeEncodeError):
        async with wrapper.request_stream(_surrogate_msg(), None, _mrp()):
            pass
    assert len(fake.stream_calls) == 2


@pytest.mark.asyncio
async def test_request_stream_propagates_post_open_consumer_error():
    """Consumer-side UnicodeEncodeError (raised after stream opened) propagates — no silent recovery."""
    fake = _FakeModel(raise_n_times=0)
    wrapper = SurrogateRecoveryModel(fake)

    async def _consume() -> None:
        async with wrapper.request_stream(_clean_msg(), None, _mrp()):
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "consumer side")

    with pytest.raises(UnicodeEncodeError):
        await _consume()
    assert len(fake.stream_calls) == 1, "no retry — exception happened after open"


@pytest.fixture
def isolated_spans_log(tmp_path: Path):
    """Isolated spans log with clean state; restores logger state on teardown."""
    logger = logging.getLogger("co_cli.observability.spans")
    saved_handlers = list(logger.handlers)
    saved_patterns = list(tracing._COMPILED_PATTERNS)
    for h in saved_handlers:
        logger.removeHandler(h)
    tracing._SPAN_STACK.set(())

    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)
    yield log

    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    for h in saved_handlers:
        logger.addHandler(h)
    tracing._COMPILED_PATTERNS = saved_patterns


def _events_on(log: Path, span_name: str) -> list[str]:
    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    span = next(r for r in records if r["name"] == span_name)
    return [e["name"] for e in span["events"]]


@pytest.mark.asyncio
async def test_request_recovery_emits_event_on_active_span(isolated_spans_log: Path):
    """A request-path recovery attaches a surrogate_recovery event to the active span."""
    fake = _FakeModel(raise_n_times=1)
    wrapper = SurrogateRecoveryModel(fake)
    tracing.push_span("test_parent", kind="model")
    try:
        await wrapper.request(_surrogate_msg(), None, _mrp())
    finally:
        tracing.pop_span()
    assert "surrogate_recovery" in _events_on(isolated_spans_log, "test_parent")


@pytest.mark.asyncio
async def test_request_stream_recovery_emits_event_on_active_span(isolated_spans_log: Path):
    """A stream-path recovery attaches a surrogate_recovery event to the active span."""
    fake = _FakeModel(raise_n_times=1)
    wrapper = SurrogateRecoveryModel(fake)
    tracing.push_span("test_parent", kind="model")
    try:
        async with wrapper.request_stream(_surrogate_msg(), None, _mrp()) as stream:
            assert isinstance(stream, StreamedResponse)
    finally:
        tracing.pop_span()
    assert "surrogate_recovery" in _events_on(isolated_spans_log, "test_parent")
