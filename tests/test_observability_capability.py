"""Tests for ObservabilityCapability — end-to-end record emission via TestModel."""

import json
import logging
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from co_cli.observability import tracing
from co_cli.observability.capability import ObservabilityCapability


@pytest.fixture(autouse=True)
def _reset_tracing(tmp_path: Path) -> None:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    tracing._COMPILED_PATTERNS = []
    tracing._SESSION_ID.set(None)
    tracing._TRACE_ID.set(None)
    tracing._SPAN_STACK.set(())


def _read_records(log_path: Path) -> list[dict]:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in logger.handlers:
        handler.flush()
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _records_by_kind(records: list[dict], kind: str) -> list[dict]:
    return [r for r in records if r["kind"] == kind]


def _make_function_model(responder) -> FunctionModel:
    """Wrap a sync responder as a FunctionModel."""

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return responder(messages, info)

    return FunctionModel(fn)


@pytest.mark.asyncio
async def test_one_turn_emits_agent_model_tool_records(tmp_path: Path) -> None:
    """A single agent.run that calls one tool then answers must emit:
    1 agent + 2 model + 1 tool record, with correct parent linkage."""
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    call_count = {"n": 0}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="echo", args={"text": "hi"}, tool_call_id="c1")],
                model_name="fn-test",
            )
        return ModelResponse(
            parts=[TextPart(content="done")],
            model_name="fn-test",
        )

    agent: Agent = Agent(
        _make_function_model(respond),
        capabilities=[ObservabilityCapability()],
    )

    @agent.tool_plain
    def echo(text: str) -> str:
        return f"echoed: {text}"

    result = await agent.run("hello", metadata={"role": "test-agent", "request_limit": 5})
    assert result.output == "done"

    records = _read_records(log)
    agent_records = _records_by_kind(records, "agent")
    model_records = _records_by_kind(records, "model")
    tool_records = _records_by_kind(records, "tool")

    assert len(agent_records) == 1, f"expected 1 agent record, got {len(agent_records)}"
    assert len(model_records) == 2, f"expected 2 model records, got {len(model_records)}"
    assert len(tool_records) == 1, f"expected 1 tool record, got {len(tool_records)}"

    agent_id = agent_records[0]["span_id"]
    for r in model_records + tool_records:
        assert r["parent_span_id"] == agent_id, (
            f"{r['name']} parent_span_id={r['parent_span_id']} != agent span_id={agent_id}"
        )


@pytest.mark.asyncio
async def test_tool_error_path_records_error_and_clears_stack(tmp_path: Path) -> None:
    """A tool that raises produces a tool ERROR record AND the span stack
    is empty after the exception propagates (no leak across runs)."""
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    call_count = {"n": 0}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="boom", args={}, tool_call_id="t1")],
                model_name="fn",
            )
        return ModelResponse(parts=[TextPart(content="recovered")], model_name="fn")

    agent: Agent = Agent(_make_function_model(respond), capabilities=[ObservabilityCapability()])

    @agent.tool_plain
    def boom() -> str:
        raise ValueError("tool exploded")

    try:
        await agent.run("boom please")
    except Exception:
        pass

    assert tracing._SPAN_STACK.get() == (), "span stack must be empty after exception"

    tool_records = _records_by_kind(_read_records(log), "tool")
    error_recs = [r for r in tool_records if r["status"] == "ERROR"]
    assert len(error_recs) >= 1, "expected at least one ERROR tool record"
    assert "tool exploded" in error_recs[0]["status_msg"]
