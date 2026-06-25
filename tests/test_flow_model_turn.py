"""Tests for the graph-free model-turn client ``model_turn``.

Two layers:
  - Fake-model functional tests (this module's first half): drive ``model_turn``
    through ``direct.model_request_stream`` against a fake ``Model`` and assert
    observable outcomes — recovered response delivered, surrogate replaced in the
    content the provider receives, fatal error surfaced, repaired args parse as
    JSON, span artifacts emitted. Fakes are required because a real model cannot
    be made to reliably emit a lone surrogate or malformed JSON.
  - One real-Ollama streamed integration test (second half): proves the client
    streams a real model response end-to-end with no wrapper involved.
"""

from __future__ import annotations

import asyncio
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
    PartDeltaEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

from co_cli.llm.factory import build_model
from co_cli.llm.model_turn import model_turn
from co_cli.observability import tracing

# ---------------------------------------------------------------------------
# Fakes — a Model whose stream returns a programmed assembled response
# ---------------------------------------------------------------------------


class _FakeStream(StreamedResponse):
    """StreamedResponse whose assembled get() returns a programmed response."""

    def __init__(
        self,
        mrp: ModelRequestParameters,
        response: ModelResponse,
        deltas: tuple[str, ...] = (),
    ) -> None:
        super().__init__(mrp)
        self._response = response
        self._deltas = deltas

    async def _get_event_iterator(self) -> AsyncIterator[Any]:
        for i, text in enumerate(self._deltas):
            yield PartDeltaEvent(index=i, delta=TextPartDelta(content_delta=text))

    def get(self) -> ModelResponse:
        return self._response

    def usage(self) -> RequestUsage:
        return RequestUsage()

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


class _RecordingModel(Model):
    """Fake model recording the messages each stream-open received.

    ``raise_n_opens`` programs the first N stream opens to raise
    ``UnicodeEncodeError`` (during ``__aenter__`` — i.e. around open), modelling a
    surrogate that survives until sanitize-retry.
    """

    def __init__(self, response: ModelResponse, *, raise_n_opens: int = 0) -> None:
        super().__init__()
        self._response = response
        self._raise_n_opens = raise_n_opens
        self.stream_messages: list[list[ModelMessage]] = []

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def system(self) -> str:
        return "fake"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        raise NotImplementedError("streaming-only fake")

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[StreamedResponse]:
        self.stream_messages.append(messages)
        if len(self.stream_messages) <= self._raise_n_opens:
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")
        yield _FakeStream(model_request_parameters, self._response, deltas=("hi",))

    async def count_tokens(self, *args: Any, **kwargs: Any) -> RequestUsage:
        return RequestUsage()


def _mrp() -> ModelRequestParameters:
    return ModelRequestParameters()


def _text_response(text: str = "ok") -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)], model_name="fake")


def _malformed_tool_response() -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name="shell_exec", args='{"cmd": "ls",', tool_call_id="c1")],
        model_name="fake",
    )


def _surrogate_msg() -> list[ModelMessage]:
    return [ModelRequest(parts=[UserPromptPart(content="hello\ud800world")])]


def _clean_msg() -> list[ModelMessage]:
    return [ModelRequest(parts=[UserPromptPart(content="hello world")])]


# ---------------------------------------------------------------------------
# (a) clean input — the answer is delivered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_input_delivers_response():
    model = _RecordingModel(_text_response("answer"))
    async with model_turn(model, _clean_msg(), _mrp(), None, repair=False) as stream:
        response = stream.get()
    assert "".join(p.content for p in response.parts if isinstance(p, TextPart)) == "answer"


# ---------------------------------------------------------------------------
# (b) lone-surrogate input — recovery delivers a response AND the content the
#     provider receives on the successful attempt has the surrogate replaced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_surrogate_input_recovered_with_clean_content_reaching_model():
    model = _RecordingModel(_text_response("answer"), raise_n_opens=1)
    async with model_turn(model, _surrogate_msg(), _mrp(), None, repair=False) as stream:
        response = stream.get()
    assert isinstance(response, ModelResponse)
    successful_attempt = model.stream_messages[-1]
    delivered = successful_attempt[0]
    assert isinstance(delivered, ModelRequest)
    assert delivered.parts[0].content == "hello�world"


# ---------------------------------------------------------------------------
# (c) sanitization fails on both attempts — the error is terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unrecoverable_surrogate_error_surfaces():
    model = _RecordingModel(_text_response(), raise_n_opens=2)
    with pytest.raises(UnicodeEncodeError):
        async with model_turn(model, _surrogate_msg(), _mrp(), None, repair=False):
            pass


# ---------------------------------------------------------------------------
# (d) consumer error after the stream opened — surfaces unchanged, no retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_open_consumer_error_surfaces_without_retry():
    model = _RecordingModel(_text_response())

    async def _consume() -> None:
        async with model_turn(model, _clean_msg(), _mrp(), None, repair=False):
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "consumer side")

    with pytest.raises(UnicodeEncodeError):
        await _consume()
    assert len(model.stream_messages) == 1


# ---------------------------------------------------------------------------
# (e) repair gate — repaired args parse as JSON; verbatim when disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_true_yields_valid_json_args():
    model = _RecordingModel(_malformed_tool_response())
    async with model_turn(model, _clean_msg(), _mrp(), None, repair=True) as stream:
        response = stream.get()
    (part,) = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert json.loads(part.args) == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_repair_false_returns_args_verbatim():
    model = _RecordingModel(_malformed_tool_response())
    async with model_turn(model, _clean_msg(), _mrp(), None, repair=False) as stream:
        response = stream.get()
    (part,) = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert part.args == '{"cmd": "ls",'


# ---------------------------------------------------------------------------
# (f) span artifact — kind="model" record on success, status="ERROR" on raise
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_spans_log(tmp_path: Path):
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


def _read_records(log_path: Path) -> list[dict]:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in logger.handlers:
        handler.flush()
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_successful_turn_emits_model_span(isolated_spans_log: Path):
    model = _RecordingModel(_text_response("answer"))
    async with model_turn(model, _clean_msg(), _mrp(), None, repair=False) as stream:
        stream.get()
    model_recs = [r for r in _read_records(isolated_spans_log) if r["kind"] == "model"]
    assert len(model_recs) == 1
    attrs = model_recs[0]["attributes"]
    assert "co.model.output" in attrs
    assert attrs["co.model.tokens.output"] is not None


@pytest.mark.asyncio
async def test_failed_turn_emits_error_span(isolated_spans_log: Path):
    model = _RecordingModel(_text_response(), raise_n_opens=2)
    with pytest.raises(UnicodeEncodeError):
        async with model_turn(model, _surrogate_msg(), _mrp(), None, repair=False):
            pass
    error_recs = [
        r
        for r in _read_records(isolated_spans_log)
        if r["kind"] == "model" and r["status"] == "ERROR"
    ]
    assert len(error_recs) == 1


# ---------------------------------------------------------------------------
# Real-Ollama streamed integration test — TASK-3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_turn_streams_real_ollama_response():
    """model_turn streams a real model response end-to-end with no wrapper involved.

    Skipped unless the configured provider is Ollama — the ``.wrapped`` reach-in
    below assumes the wrapped Ollama provider, and warming + repair-gating are
    Ollama-specific. The reach-in is test-only and disappears at Phase 5 when the
    factory returns a raw model.
    """
    if not SETTINGS_NO_MCP.llm.uses_ollama():
        pytest.skip("model_turn integration test assumes the wrapped Ollama provider")

    raw_model = build_model(SETTINGS_NO_MCP.llm).model.wrapped
    settings = SETTINGS_NO_MCP.llm.noreason_model_settings()
    messages = [ModelRequest.user_text_prompt("Reply with the single word: PONG")]

    await ensure_ollama_warm(TEST_LLM.model)
    deltas: list[str] = []
    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        async with model_turn(
            raw_model,
            messages,
            ModelRequestParameters(),
            settings,
            repair=SETTINGS_NO_MCP.llm.uses_ollama(),
        ) as stream:
            async for event in stream:
                if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    deltas.append(event.delta.content_delta or "")
            response = stream.get()

    streamed_text = "".join(deltas)
    assembled_text = "".join(p.content for p in response.parts if isinstance(p, TextPart))
    assert streamed_text.strip(), "text deltas must arrive during iteration"
    assert assembled_text.strip()
    assert streamed_text in assembled_text or assembled_text in streamed_text
