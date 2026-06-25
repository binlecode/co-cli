"""Tests for syntactic tool-call arg repair, homed in co_cli.llm._json_repair.

Two layers:
  - repair_json_args / repair_response: the syntactic repair, applied to each
    string ToolCallPart.args on a ModelResponse before pydantic validation.
  - SurrogateRecoveryModel.request gating: repair runs on the Ollama-backed model
    (repair_tool_args=True) and is a no-op on the Gemini path (default False).
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from co_cli.llm._json_repair import repair_json_args, repair_response
from co_cli.llm.surrogate_recovery_model import SurrogateRecoveryModel

# ---------------------------------------------------------------------------
# repair_json_args — syntactic repair passes
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
# SurrogateRecoveryModel.request — gated repair on the non-stream path
# ---------------------------------------------------------------------------


def _malformed_tool_call_model() -> FunctionModel:
    """A FunctionModel that emits a tool call with a trailing-comma args string."""

    def respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="shell_exec", args='{"cmd": "ls",', tool_call_id="c1")],
            model_name="fn",
        )

    async def fn(messages, info: AgentInfo) -> ModelResponse:
        return respond(messages, info)

    return FunctionModel(fn)


@pytest.mark.asyncio
async def test_ollama_path_repairs_malformed_args():
    """repair_tool_args=True (Ollama) produces valid JSON args ready for validation."""
    model = SurrogateRecoveryModel(_malformed_tool_call_model(), repair_tool_args=True)
    response = await model.request([], None, ModelRequestParameters())
    (part,) = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert json.loads(part.args) == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_gemini_path_leaves_args_untouched():
    """Default repair_tool_args=False (Gemini) passes the model output through verbatim."""
    model = SurrogateRecoveryModel(_malformed_tool_call_model())
    response = await model.request([], None, ModelRequestParameters())
    (part,) = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert part.args == '{"cmd": "ls",'


# ---------------------------------------------------------------------------
# SurrogateRecoveryModel.request_stream — gated repair on the streaming path
#
# The agent graph validates streamed tool args from StreamedResponse.get(), so
# RepairingStreamedResponse must repair that assembled response. These tests pin
# that seam: a future SDK change that bypasses it turns them red instead of
# silently crashing every malformed-JSON tool call on the Ollama streaming path.
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
    """Fake model whose stream yields a malformed-args assembled response."""

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
        yield _MalformedArgsStream(model_request_parameters)

    async def count_tokens(self, *args: Any, **kwargs: Any) -> RequestUsage:
        return RequestUsage()


@pytest.mark.asyncio
async def test_streaming_path_repairs_malformed_args():
    """repair_tool_args=True repairs the assembled streamed response's tool-call args."""
    model = SurrogateRecoveryModel(_StreamingModel(), repair_tool_args=True)
    async with model.request_stream([], None, ModelRequestParameters()) as stream:
        response = stream.get()
    (part,) = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert json.loads(part.args) == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_streaming_path_leaves_args_untouched_when_disabled():
    """Default repair_tool_args=False passes the streamed args through verbatim."""
    model = SurrogateRecoveryModel(_StreamingModel())
    async with model.request_stream([], None, ModelRequestParameters()) as stream:
        response = stream.get()
    (part,) = [p for p in response.parts if isinstance(p, ToolCallPart)]
    assert part.args == '{"cmd": "ls",'
