"""Tests for syntactic tool-call arg repair, relocated into SurrogateRecoveryModel.

Two layers:
  - _repair_json_args / _repair_response: the syntactic repair, applied to each
    string ToolCallPart.args on a ModelResponse before pydantic validation.
  - SurrogateRecoveryModel.request gating: repair runs on the Ollama-backed model
    (repair_tool_args=True) and is a no-op on the Gemini path (default False).
"""

import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel

from co_cli.llm.surrogate_recovery_model import (
    SurrogateRecoveryModel,
    _repair_json_args,
    _repair_response,
)

# ---------------------------------------------------------------------------
# _repair_json_args — syntactic repair passes
# ---------------------------------------------------------------------------


def test_clean_json_passes_through():
    result = _repair_json_args('{"cmd": "ls"}')
    assert json.loads(result) == {"cmd": "ls"}


def test_empty_string_becomes_empty_object():
    assert _repair_json_args("") == "{}"


def test_none_literal_becomes_empty_object():
    assert _repair_json_args("None") == "{}"


def test_trailing_comma_stripped():
    result = _repair_json_args('{"a": 1,}')
    assert json.loads(result) == {"a": 1}


def test_control_chars_escaped():
    raw = '{"cmd": "git\tstatus"}'
    result = _repair_json_args(raw)
    assert json.loads(result)["cmd"] == "git\tstatus"


def test_unclosed_brace_balanced():
    result = _repair_json_args('{"a": 1')
    assert json.loads(result) == {"a": 1}


def test_nested_unclosed_brace_balanced():
    result = _repair_json_args('{"a": {"b": 2')
    assert json.loads(result) == {"a": {"b": 2}}


def test_excess_closing_delimiters_trimmed():
    result = _repair_json_args('{"a": 1}}}')
    assert json.loads(result) == {"a": 1}


def test_combined_trailing_comma_and_unclosed():
    result = _repair_json_args('{"a": 1,')
    assert json.loads(result) == {"a": 1}


# ---------------------------------------------------------------------------
# _repair_response — repair must not corrupt already-valid tool calls
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
    repaired = _repair_response(response)
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
