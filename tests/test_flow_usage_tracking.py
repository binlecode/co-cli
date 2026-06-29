"""Flow tests for token-usage capture, fork-sharing, and per-turn flush.

All ledger I/O is real (CO_HOME-overridden temp dir). The owned turn-loop capture
chokepoint is exercised with pydantic-ai's ``FunctionModel`` (the SDK's deterministic
agent driver) — no real LLM call needed to prove coverage, keeping the flow deterministic.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RequestUsage
from tests._settings import SETTINGS

from co_cli.agent.core import assemble_routing_toolset
from co_cli.agent.loop import run_turn_owned
from co_cli.agent.turn_state import TurnResult
from co_cli.daemons.dream._loop import _flush_daemon_usage
from co_cli.deps import (
    CoDeps,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
    fork_deps,
)
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.call import llm_call
from co_cli.llm.factory import LlmModel
from co_cli.main import _apply_command_outcome, _finalize_turn
from co_cli.observability.usage import record_usage
from co_cli.session.usage import aggregate
from co_cli.tools.shell_backend import ShellBackend


def _info(name: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description="test",
        is_approval_required=False,
        source=ToolSourceEnum.NATIVE,
        visibility=VisibilityPolicyEnum.ALWAYS,
        is_concurrent_safe=True,
    )


def _make_deps(
    tmp_path: Path, *, model: object | None = None, max_model_requests: int = 90
) -> CoDeps:
    session_path = tmp_path / "sessions" / "2026-06-04T120000.000-abcd1234.jsonl"
    config = SETTINGS.model_copy(
        update={
            "llm": SETTINGS.llm.model_copy(
                update={"max_model_requests_per_turn": max_model_requests}
            )
        }
    )
    return CoDeps(
        shell=ShellBackend(),
        model=model,  # type: ignore[arg-type]
        config=config,
        session=CoSessionState(session_path=session_path),
        sessions_dir=tmp_path / "sessions",
        usage_log_path=tmp_path / "usage.jsonl",
        model_max_context_tokens=config.llm.max_context_tokens,
    )


def _ledger_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _tool_then_text_model(tool_calls: int) -> FunctionModel:
    """A model that issues ``tool_calls`` tool-call steps, then answers with text.

    Each step is one model request; the owned loop accumulates RunUsage across them
    and records the cumulative total once.
    """
    state = {"n": 0}

    async def stream_fn(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        state["n"] += 1
        if state["n"] <= tool_calls:
            yield {0: DeltaToolCall(name="noop", json_args="{}", tool_call_id=f"c{state['n']}")}
        else:
            yield "done"

    return FunctionModel(stream_function=stream_fn)


def _owned_deps(tmp_path: Path, model: FunctionModel, *, max_model_requests: int = 90) -> CoDeps:
    """Build deps wired with a noop native toolset + the supplied FunctionModel."""
    inner: FunctionToolset = FunctionToolset()

    async def noop(ctx: RunContext[CoDeps]) -> str:
        return "noop"

    inner.add_function(noop, requires_approval=False)
    deps = _make_deps(tmp_path, max_model_requests=max_model_requests)
    deps.model = LlmModel(
        model=model,
        settings=deps.config.llm.noreason_model_settings(),
        settings_noreason=deps.config.llm.noreason_model_settings(),
    )
    deps.toolset = assemble_routing_toolset(inner, [])
    deps.tool_catalog = {"noop": _info("noop")}
    return deps


@pytest.mark.asyncio
async def test_multi_step_turn_records_final_usage_once(tmp_path: Path) -> None:
    """A 2-step (tool then text) turn records the turn's FINAL cumulative usage once.

    Because RunUsage is cumulative across steps, the accumulator equalling turn.usage
    proves the turn recorded once at the boundary, not per-step (which would double-count).
    """
    deps = _owned_deps(tmp_path, _tool_then_text_model(tool_calls=1))

    turn = await run_turn_owned(
        user_input="ping",
        deps=deps,
        message_history=[],
        frontend=HeadlessFrontend(approval_response="y"),
    )

    assert turn.outcome == "continue"
    assert turn.usage is not None
    assert turn.usage.input_tokens > 0
    assert deps.usage_accumulator.input_tokens == turn.usage.input_tokens
    assert deps.usage_accumulator.output_tokens == turn.usage.output_tokens


@pytest.mark.asyncio
async def test_error_outcome_turn_still_records_usage(tmp_path: Path) -> None:
    """A cap-stopped (error-outcome) turn still records its usage — the record sits in
    the finally block that catches every return path, not the happy path only."""
    deps = _owned_deps(tmp_path, _tool_then_text_model(tool_calls=5), max_model_requests=2)

    turn = await run_turn_owned(
        user_input="ping",
        deps=deps,
        message_history=[],
        frontend=HeadlessFrontend(approval_response="y"),
    )

    assert turn.outcome == "error"
    assert deps.usage_accumulator.input_tokens > 0, "usage must be recorded even on error outcome"


@pytest.mark.asyncio
async def test_llm_call_post_response_bumps_accumulator(tmp_path: Path) -> None:
    """The direct llm_call path records the response's usage into the accumulator."""
    deps = _make_deps(tmp_path)

    def respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="pong")],
            model_name="fn",
            usage=RequestUsage(input_tokens=33, output_tokens=5),
        )

    async def fn(messages, info: AgentInfo) -> ModelResponse:
        return respond(messages, info)

    fake_model = SimpleNamespace(model=FunctionModel(fn), settings_noreason=None)

    await llm_call(deps, "ping", model=fake_model)

    assert deps.usage_accumulator.input_tokens == 33
    assert deps.usage_accumulator.output_tokens == 5


def test_fork_shares_accumulator_so_subagent_tokens_roll_up(tmp_path: Path) -> None:
    """A forked child shares the parent accumulator — both records sum into one tally."""
    deps = _make_deps(tmp_path)
    child = fork_deps(deps)

    record_usage(deps, RequestUsage(input_tokens=10, output_tokens=1))
    record_usage(child, RequestUsage(input_tokens=20, output_tokens=2))

    assert child.usage_accumulator is deps.usage_accumulator
    assert deps.usage_accumulator.input_tokens == 30
    assert deps.usage_accumulator.output_tokens == 3


@pytest.mark.asyncio
async def test_finalize_turn_flushes_one_line_and_resets(tmp_path: Path) -> None:
    """_finalize_turn appends exactly one session-origin ledger line and resets the accumulator."""
    deps = _make_deps(tmp_path)
    deps.usage_accumulator.add(120, 15)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(parts=[TextPart(content="hi")], model_name="fn"),
    ]
    turn_result = TurnResult(outcome="continue", interrupted=False, messages=messages)

    await _finalize_turn(turn_result, [], deps, HeadlessFrontend())

    lines = _ledger_lines(deps.usage_log_path)
    assert len(lines) == 1
    assert lines[0]["origin"] == "session"
    assert lines[0]["session_id"] == "abcd1234"
    assert lines[0]["input_tokens"] == 120
    assert lines[0]["output_tokens"] == 15
    assert deps.usage_accumulator.input_tokens == 0
    assert deps.usage_accumulator.output_tokens == 0


def test_compact_branch_flushes_its_own_line_and_resets(tmp_path: Path) -> None:
    """The /compact (compaction_applied) outcome flushes its summarizer tokens, not the next turn's."""
    from co_cli.commands.types import ReplaceTranscript

    deps = _make_deps(tmp_path)
    deps.usage_accumulator.add(200, 25)
    history = [ModelResponse(parts=[TextPart(content="summary")], model_name="fn")]
    outcome = ReplaceTranscript(history=history, compaction_applied=True)

    _apply_command_outcome(outcome, [], deps, HeadlessFrontend())

    lines = _ledger_lines(deps.usage_log_path)
    assert len(lines) == 1
    assert lines[0]["origin"] == "session"
    assert lines[0]["input_tokens"] == 200
    assert lines[0]["output_tokens"] == 25
    assert deps.usage_accumulator.input_tokens == 0
    assert deps.usage_accumulator.output_tokens == 0


def test_daemon_flush_writes_daemon_line_excluded_from_session_subtotal(tmp_path: Path) -> None:
    """The daemon cycle flush appends a daemon-origin line: counted in the combined
    total, never in the session subtotal nor a current-session aggregate."""
    deps = _make_deps(tmp_path)
    deps.usage_accumulator.add(90, 11)

    _flush_daemon_usage(deps)

    lines = _ledger_lines(deps.usage_log_path)
    assert len(lines) == 1
    assert lines[0]["origin"] == "daemon"
    assert lines[0]["session_id"] is None
    assert deps.usage_accumulator.input_tokens == 0
    assert deps.usage_accumulator.output_tokens == 0

    window = aggregate(deps.usage_log_path)
    assert window.total.input_tokens == 90
    assert window.total.output_tokens == 11
    assert window.session.input_tokens == 0
    assert window.session.output_tokens == 0
    assert window.daemon.input_tokens == 90
    assert window.daemon.output_tokens == 11

    session_window = aggregate(deps.usage_log_path, session_id="abcd1234", origin="session")
    assert session_window.total.total == 0
