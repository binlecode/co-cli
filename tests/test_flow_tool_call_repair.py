"""Model-boundary JSON-arg repair + surrogate recovery, homed in ``model_turn``.

Three layers, all owned by the owned-loop model client (``co_cli.llm.model_turn``)
after the legacy graph model-wrapper removal:

  - ``repair_json_args`` / ``repair_response`` (``co_cli.llm._json_repair``): purely
    syntactic repair applied to each string ``ToolCallPart.args`` before pydantic
    validation. Pure functions — asserted directly.
  - ``model_turn(..., repair=True)``: drives the assembled streamed response through
    ``RepairingStreamedResponse`` so malformed Ollama tool args are repaired at the
    ``.get()`` surface the loop validates from; ``repair=False`` (Gemini) passes through.
  - ``model_turn`` surrogate recovery: a ``UnicodeEncodeError`` raised on stream open is
    caught, messages are re-sanitized, and the stream is retried once. A consumer-side
    error raised after the stream opened propagates unchanged (no retry).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.usage import RequestUsage

from co_cli.llm._json_repair import repair_json_args, repair_response
from co_cli.llm.model_turn import model_turn

# ---------------------------------------------------------------------------
# repair_json_args — syntactic repair passes (pure function)
# ---------------------------------------------------------------------------


def test_clean_json_passes_through():
    result = repair_json_args('{"cmd": "ls"}')
    assert json.loads(result) == {"cmd": "ls"}


def test_empty_string_becomes_empty_object():
    assert repair_json_args("") == "{}"


def test_none_literal_becomes_empty_object():
    assert repair_json_args("None") == "{}"


def test_trailing_comma_stripped():
    result = repair_json_args('{"a": 1,}')
    assert json.loads(result) == {"a": 1}


def test_control_chars_escaped():
    raw = '{"cmd": "git\tstatus"}'
    result = repair_json_args(raw)
    assert json.loads(result)["cmd"] == "git\tstatus"


def test_unclosed_brace_balanced():
    result = repair_json_args('{"a": 1')
    assert json.loads(result) == {"a": 1}


def test_nested_unclosed_brace_balanced():
    result = repair_json_args('{"a": {"b": 2')
    assert json.loads(result) == {"a": {"b": 2}}


def test_excess_closing_delimiters_trimmed():
    result = repair_json_args('{"a": 1}}}')
    assert json.loads(result) == {"a": 1}


def test_combined_trailing_comma_and_unclosed():
    result = repair_json_args('{"a": 1,')
    assert json.loads(result) == {"a": 1}


# ---------------------------------------------------------------------------
# repair_response — repair must not corrupt already-valid tool calls
# ---------------------------------------------------------------------------


def test_repair_response_leaves_valid_and_dict_args_intact():
    """Valid string args and dict args survive repair unchanged (no corruption)."""
    response = ModelResponse(
        parts=[
            ToolCallPart(tool_name="a", args='{"x": 1}', tool_call_id="c1"),
            ToolCallPart(tool_name="b", args={"y": 2}, tool_call_id="c2"),
        ],
        model_name="fn",
    )
    repaired = repair_response(response)
    parts = [p for p in repaired.parts if isinstance(p, ToolCallPart)]
    assert json.loads(parts[0].args) == {"x": 1}
    assert parts[1].args == {"y": 2}


# ---------------------------------------------------------------------------
# model_turn(repair=...) — gated repair on the assembled streamed response.
#
# The owned loop validates tool args from the assembled StreamedResponse.get(),
# so model_turn must repair that response when repair=True (Ollama). These pin
# the seam end-to-end: a regression that bypasses it turns them red instead of
# silently crashing every malformed-JSON tool call on the Ollama path.
# ---------------------------------------------------------------------------


class _MalformedArgsStream(StreamedResponse):
    """StreamedResponse whose assembled get() carries a malformed-JSON ToolCallPart."""

    def __init__(self, mrp: ModelRequestParameters) -> None:
        super().__init__(mrp)

    async def _get_event_iterator(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    def get(self) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="shell_exec", args='{"cmd": "ls",', tool_call_id="c1")],
            model_name="fake",
        )

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


class _StreamingModel(Model):
    """Fake provider model whose stream yields a malformed-args assembled response."""

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def system(self) -> str:
        return "fake"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: Any,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        raise NotImplementedError("streaming-only fake")

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: Any,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[StreamedResponse]:
        yield _MalformedArgsStream(model_request_parameters)

    async def count_tokens(self, *args: Any, **kwargs: Any) -> RequestUsage:
        return RequestUsage()


@pytest.mark.asyncio
async def test_model_turn_repairs_malformed_streamed_args_when_enabled():
    """repair=True (Ollama) repairs the assembled streamed response's tool-call args."""
    async with model_turn(
        _StreamingModel(), [], ModelRequestParameters(), None, repair=True
    ) as stream:
        async for _ in stream:
            pass
        response = stream.get()
    (part,) = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert json.loads(part.args) == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_model_turn_leaves_streamed_args_untouched_when_disabled():
    """repair=False (Gemini) passes the streamed args through verbatim."""
    async with model_turn(
        _StreamingModel(), [], ModelRequestParameters(), None, repair=False
    ) as stream:
        async for _ in stream:
            pass
        response = stream.get()
    (part,) = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert part.args == '{"cmd": "ls",'


# ---------------------------------------------------------------------------
# model_turn — surrogate recovery on UnicodeEncodeError around stream open.
# ---------------------------------------------------------------------------


class _CleanStream(StreamedResponse):
    """Minimal StreamedResponse with no events — for open/close path testing only."""

    def __init__(self, mrp: ModelRequestParameters) -> None:
        super().__init__(mrp)

    async def _get_event_iterator(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    def get(self) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(tool_name="t", args="{}", tool_call_id="c1")])

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


class _SurrogateOnOpenModel(Model):
    """Fake model that raises UnicodeEncodeError on stream open for its first N opens.

    Records the messages handed to each open so the test can assert the retry was
    driven with sanitized (surrogate-stripped) content.
    """

    def __init__(self, raise_n_times: int = 0) -> None:
        super().__init__()
        self.raise_n_times = raise_n_times
        self.opens: list[list[ModelMessage]] = []

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def system(self) -> str:
        return "fake"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: Any,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        raise NotImplementedError("streaming-only fake")

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: Any,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[StreamedResponse]:
        self.opens.append(messages)
        if len(self.opens) <= self.raise_n_times:
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")
        yield _CleanStream(model_request_parameters)


def _surrogate_msgs() -> list[ModelMessage]:
    return [ModelRequest(parts=[UserPromptPart(content="hello\ud800world")])]


def _clean_msgs() -> list[ModelMessage]:
    return [ModelRequest(parts=[UserPromptPart(content="hello world")])]


@pytest.mark.asyncio
async def test_model_turn_recovers_surrogate_error_on_open():
    """A UnicodeEncodeError on stream open triggers a sanitize-retry that succeeds.

    The retry must be driven with surrogate-stripped messages — the lone U+D800 is
    replaced with U+FFFD before the second open.
    """
    model = _SurrogateOnOpenModel(raise_n_times=1)
    async with model_turn(model, _surrogate_msgs(), ModelRequestParameters(), None, repair=False):
        pass
    assert len(model.opens) == 2, "first open raised, second succeeded after sanitize"
    retry_part = model.opens[1][0].parts[0]
    assert isinstance(retry_part, UserPromptPart)
    assert retry_part.content == "hello�world"


@pytest.mark.asyncio
async def test_model_turn_clean_open_does_not_retry():
    """A clean open reaches the stream on the first try — no sanitize-retry."""
    model = _SurrogateOnOpenModel(raise_n_times=0)
    async with model_turn(model, _clean_msgs(), ModelRequestParameters(), None, repair=False):
        pass
    assert len(model.opens) == 1


@pytest.mark.asyncio
async def test_model_turn_propagates_when_retry_also_raises():
    """If both opens raise UnicodeEncodeError, the error propagates (no infinite retry)."""
    model = _SurrogateOnOpenModel(raise_n_times=2)
    with pytest.raises(UnicodeEncodeError):
        async with model_turn(
            model, _surrogate_msgs(), ModelRequestParameters(), None, repair=False
        ):
            pass
    assert len(model.opens) == 2, "one retry only — not retried again after the second failure"


@pytest.mark.asyncio
async def test_model_turn_propagates_post_open_consumer_error():
    """A UnicodeEncodeError raised after the stream opened propagates — no silent recovery."""
    model = _SurrogateOnOpenModel(raise_n_times=0)
    with pytest.raises(UnicodeEncodeError):
        async with model_turn(model, _clean_msgs(), ModelRequestParameters(), None, repair=False):
            raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "consumer side")
    assert len(model.opens) == 1, "no retry — exception happened after open"
