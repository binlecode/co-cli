"""Owned-loop error/recovery parity tests (Phase 4).

Each behavior the owned loop relocates from the graph path has a graph-path twin
(``test_flow_orchestrate_*``, ``test_flow_compaction_recovery``); these assert the same
observable behavior on the owned path — the same user-facing status messages, the same
retry/terminal outcome, the same history shape. Deterministic error injection uses
pydantic-ai's ``FunctionModel`` (the SDK's test double, the same vehicle the graph twins
use); the length-continuation retry is exercised against real Ollama (the graph twin is
real-LLM too), with a no-LLM behavioral pin on the retry decision for the tool-call branch.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage, RunUsage
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import (
    LLM_COMPACTION_SUMMARY_TIMEOUT_SECS,
    PYTEST_PER_TEST_TIMEOUT_SECS,
)

from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
from co_cli.agent.loop import (
    _emit_output_limit_diagnostics,
    _interrupted_result,
    run_turn_owned,
)
from co_cli.agent.preflight import (
    clean_message_history,
    fill_unanswered_tool_calls,
    run_history_processors,
)
from co_cli.agent.recovery import length_retry_settings
from co_cli.agent.turn_state import TurnState
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import LlmModel, build_model
from co_cli.observability import tracing
from co_cli.tools.shell_backend import ShellBackend

_NATIVE, _CATALOG = build_native_toolset()

_GENERIC_MESSAGE = "Provider error — turn ended:"


@pytest.fixture(autouse=True)
def _reset_tracing() -> None:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    tracing._SESSION_ID.set(None)
    tracing._TRACE_ID.set(None)
    tracing._SPAN_STACK.set(())


def _make_deps(model_obj: object, *, max_context_tokens: int | None = None) -> CoDeps:
    cfg = _CONFIG_NO_MCP
    return CoDeps(
        shell=ShellBackend(),
        model=LlmModel(
            model=model_obj,
            settings=cfg.llm.noreason_model_settings(),
            settings_noreason=cfg.llm.noreason_model_settings(),
        ),
        toolset=assemble_routing_toolset(_NATIVE, []),
        tool_catalog=_CATALOG,
        config=cfg,
        session=CoSessionState(),
        model_max_context_tokens=max_context_tokens or cfg.llm.max_context_tokens,
    )


def _fail_then_succeed(make_exc, fail_count: int) -> FunctionModel:
    """Model that raises ``make_exc()`` on its first ``fail_count`` calls, then answers."""
    state = {"calls": 0}

    async def stream_fn(messages: list, info: AgentInfo) -> AsyncIterator[str]:
        state["calls"] += 1
        if state["calls"] <= fail_count:
            raise make_exc()
        yield "All set."

    return FunctionModel(stream_function=stream_fn)


async def _run(deps: CoDeps, frontend: HeadlessFrontend, *, user_input: str = "go"):
    return await run_turn_owned(
        user_input=user_input,
        deps=deps,
        message_history=[],
        frontend=frontend,
    )


# ---------------------------------------------------------------------------
# TASK-1 — typed terminal classification + verbatim graph parity messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_ends_terminal_with_doctor_message() -> None:
    """A TimeoutError surfaces the graph's timeout wording (incl. the /doctor tail)."""
    deps = _make_deps(_fail_then_succeed(lambda: TimeoutError("stalled"), 1))
    frontend = HeadlessFrontend()

    turn = await _run(deps, frontend)

    assert turn.outcome == "error"
    assert any(
        s == "LLM call timed out — model did not respond in time."
        " Try a shorter prompt, or ask Co 'what can you do right now?' or run /doctor."
        for s in frontend.statuses
    ), frontend.statuses
    assert not any(_GENERIC_MESSAGE in s for s in frontend.statuses)


@pytest.mark.asyncio
async def test_network_error_ends_terminal_with_network_message() -> None:
    """A non-timeout ModelAPIError surfaces the graph's ``Network error:`` wording."""
    deps = _make_deps(_fail_then_succeed(lambda: ModelAPIError("fn", "boom"), 1))
    frontend = HeadlessFrontend()

    turn = await _run(deps, frontend)

    assert turn.outcome == "error"
    assert any(s.startswith("Network error:") for s in frontend.statuses), frontend.statuses
    assert not any(_GENERIC_MESSAGE in s for s in frontend.statuses)


@pytest.mark.asyncio
async def test_malformed_output_ends_terminal_with_malformed_message() -> None:
    """An UnexpectedModelBehavior surfaces the graph's malformed-output wording."""
    deps = _make_deps(_fail_then_succeed(lambda: UnexpectedModelBehavior("weird"), 1))
    frontend = HeadlessFrontend()

    turn = await _run(deps, frontend)

    assert turn.outcome == "error"
    assert any(s.startswith("Model returned malformed output:") for s in frontend.statuses), (
        frontend.statuses
    )
    assert not any(_GENERIC_MESSAGE in s for s in frontend.statuses)


# ---------------------------------------------------------------------------
# TASK-2 — overflow strip-then-summarize + HTTP 400 reflection
# ---------------------------------------------------------------------------


def _overflow_error() -> ModelHTTPError:
    return ModelHTTPError(status_code=413, model_name="fn", body={"error": "prompt is too long"})


def _tool_call_400() -> ModelHTTPError:
    return ModelHTTPError(
        status_code=400, model_name="fn", body={"error": "invalid tool call arguments"}
    )


@pytest.mark.asyncio
async def test_overflow_compacts_and_retries_to_completion() -> None:
    """A single context-overflow error compacts history once, then the retry completes."""
    deps = _make_deps(_fail_then_succeed(_overflow_error, 1))
    frontend = HeadlessFrontend()

    turn = await _run(deps, frontend)

    assert turn.outcome == "continue"
    assert any("compacting and retrying" in s for s in frontend.statuses), frontend.statuses


@pytest.mark.asyncio
async def test_second_consecutive_overflow_terminates_unrecoverable() -> None:
    """Overflow recovery is latched once per turn; a second overflow ends the turn."""
    deps = _make_deps(_fail_then_succeed(_overflow_error, 2))
    frontend = HeadlessFrontend()

    turn = await _run(deps, frontend)

    assert turn.outcome == "error"
    assert any(s == "Context overflow — unrecoverable." for s in frontend.statuses), (
        frontend.statuses
    )


@pytest.mark.asyncio
async def test_http_400_reflects_then_completes_within_budget() -> None:
    """An HTTP 400 tool-call rejection appends the reflection nudge and retries to success."""
    deps = _make_deps(_fail_then_succeed(_tool_call_400, 1))
    frontend = HeadlessFrontend()

    turn = await _run(deps, frontend)

    assert turn.outcome == "continue"
    assert any("reflecting to model" in s for s in frontend.statuses), frontend.statuses
    nudge_present = any(
        isinstance(part, UserPromptPart)
        and isinstance(part.content, str)
        and "rejected by the model provider" in part.content
        for msg in turn.messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
    )
    assert nudge_present, "reflection nudge must be appended to history"


@pytest.mark.asyncio
async def test_http_400_terminates_when_reflection_budget_exhausted() -> None:
    """Three consecutive 400s exhaust the per-turn reflection budget (2) → terminal."""
    deps = _make_deps(_fail_then_succeed(_tool_call_400, 3))
    frontend = HeadlessFrontend()

    turn = await _run(deps, frontend)

    assert turn.outcome == "error"
    assert any(s.startswith("Provider error (HTTP 400):") for s in frontend.statuses), (
        frontend.statuses
    )


# ---------------------------------------------------------------------------
# TASK-3 — length-continuation retry
# ---------------------------------------------------------------------------


def test_length_retry_decision_boosts_text_truncation_only() -> None:
    """The retry decision boosts a text truncation but refuses a tool-call-only one.

    A truncated tool-call carries malformed JSON into an unanswered tool_calls entry the
    provider rejects, so it must NOT retry (it falls through to the ceiling diagnostics).
    """
    settings = {"max_tokens": 4096, "extra_body": {"max_tokens": 4096}}

    text_trunc = ModelResponse(parts=[TextPart(content="par")], finish_reason="length")
    boosted = length_retry_settings(text_trunc, settings)
    assert boosted is not None
    assert boosted["max_tokens"] == 8192

    tool_trunc = ModelResponse(
        parts=[ToolCallPart(tool_name="x", args={}, tool_call_id="c1")],
        finish_reason="length",
    )
    assert length_retry_settings(tool_trunc, settings) is None


@pytest.mark.asyncio
@pytest.mark.timeout(PYTEST_PER_TEST_TIMEOUT_SECS + 20)
@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="length-retry parity needs Ollama"
)
async def test_length_retry_completes_truncated_owned_response() -> None:
    """A tight max_tokens forces truncation; the owned loop boosts and converges (graph parity).

    Asserts the turn succeeds, ≥2 LLM calls fired (the retry), the prompt appears exactly once
    (the truncated partial was discarded, not re-asked), and history ends on the complete
    ModelResponse.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    noreason = _CONFIG_NO_MCP.llm.noreason_model_settings()
    extra_body = {**noreason.get("extra_body", {}), "max_tokens": 80}
    constrained = {**noreason, "max_tokens": 80, "extra_body": extra_body}

    deps = CoDeps(
        shell=ShellBackend(),
        model=build_model(_CONFIG_NO_MCP.llm),
        toolset=assemble_routing_toolset(_NATIVE, []),
        tool_catalog=_CATALOG,
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
    )
    frontend = HeadlessFrontend()

    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS * 2):
        turn = await run_turn_owned(
            user_input=(
                "Write a 5-paragraph essay about why Python is popular. "
                "Each paragraph must be at least 4 sentences."
            ),
            deps=deps,
            message_history=[],
            model_settings=constrained,  # type: ignore[arg-type]
            frontend=frontend,
        )

    assert turn.outcome == "continue"
    assert turn.model_requests >= 2, turn.model_requests
    essay_prompt = "Write a 5-paragraph essay about why Python is popular. "
    occurrences = sum(
        1
        for m in turn.messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
        and isinstance(p.content, str)
        and essay_prompt in p.content
    )
    assert occurrences == 1, occurrences
    assert isinstance(turn.messages[-1], ModelResponse)


# ---------------------------------------------------------------------------
# TASK-4 — fill-unanswered net + interrupt abort marker (cross-turn boundary)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_retains_unanswered_call_and_next_turn_request_is_protocol_valid() -> None:
    """Interrupt mid-dispatch retains the unanswered response + appends the abort marker;
    the next turn's cleaned request carries a synthetic tool return before the abort prompt.
    """
    unanswered = ModelResponse(
        parts=[ToolCallPart(tool_name="file_read", args={"path": "/a"}, tool_call_id="c1")]
    )
    state = TurnState(history=[ModelRequest(parts=[UserPromptPart(content="do it")]), unanswered])

    interrupted = _interrupted_result(state, RunUsage())

    assert interrupted.interrupted is True
    assert isinstance(interrupted.messages[-1], ModelRequest)
    abort_text = "The user interrupted the previous turn"
    assert any(
        isinstance(p, UserPromptPart) and isinstance(p.content, str) and abort_text in p.content
        for p in interrupted.messages[-1].parts
    )
    assert interrupted.messages[-2] is unanswered

    # Next turn: REPL appends the new user input, then preflight runs.
    next_history = [
        *interrupted.messages,
        ModelRequest(parts=[UserPromptPart(content="continue please")]),
    ]
    deps = _make_deps(_fail_then_succeed(lambda: TimeoutError("unused"), 0))
    processed = await run_history_processors(next_history, deps)
    processed = fill_unanswered_tool_calls(processed)
    request = clean_message_history(processed)

    return_ids = {
        p.tool_call_id
        for m in request
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    }
    assert "c1" in return_ids, "synthetic tool return must answer the interrupted call"

    # The synthetic return sorts before the abort UserPromptPart inside the merged request.
    merged = next(
        m
        for m in request
        if isinstance(m, ModelRequest)
        and any(isinstance(p, ToolReturnPart) for p in m.parts)
        and any(
            isinstance(p, UserPromptPart) and "interrupted the previous turn" in str(p.content)
            for p in m.parts
        )
    )
    return_index = next(i for i, p in enumerate(merged.parts) if isinstance(p, ToolReturnPart))
    abort_index = next(
        i
        for i, p in enumerate(merged.parts)
        if isinstance(p, UserPromptPart) and "interrupted the previous turn" in str(p.content)
    )
    assert return_index < abort_index


def test_fill_unanswered_is_noop_when_calls_already_answered() -> None:
    """Intra-turn (every dispatch appends a return) the net is a no-op — no duplicate stubs."""
    history = [
        ModelRequest(parts=[UserPromptPart(content="go")]),
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="t", tool_call_id="c1", content="ok")]),
    ]

    assert fill_unanswered_tool_calls(history) == history


# ---------------------------------------------------------------------------
# TASK-5 — post-turn output-limit diagnostics
# ---------------------------------------------------------------------------


def test_diagnostics_emit_context_limit_nudge_and_span_event(tmp_path: Path) -> None:
    """A final response whose input tokens exceed the window emits the limit nudge + span event."""
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)
    deps = _make_deps(object(), max_context_tokens=100)
    response = ModelResponse(
        parts=[TextPart(content="hi")],
        finish_reason="stop",
        usage=RequestUsage(input_tokens=150),
    )
    frontend = HeadlessFrontend()

    tracing.push_span("co.turn", kind="co")
    _emit_output_limit_diagnostics(response, deps, frontend, tracing.current_span())
    tracing.pop_span()

    assert any("Context limit reached (150 / 100 tokens)" in s for s in frontend.statuses), (
        frontend.statuses
    )
    for handler in logging.getLogger("co_cli.observability.spans").handlers:
        handler.flush()
    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert any(
        event["name"] == "ctx_overflow_check"
        for record in records
        for event in record.get("events", [])
    ), records


def test_diagnostics_emit_auto_compaction_paused_nudge() -> None:
    """Between the compaction ratio and the limit, with thrash latched, the paused nudge fires."""
    deps = _make_deps(object(), max_context_tokens=1000)
    ratio = deps.config.compaction.compaction_ratio
    deps.runtime.consecutive_low_yield_proactive_compactions = (
        deps.config.compaction.proactive_thrash_window
    )
    input_tokens = int(1000 * ratio) + 10
    response = ModelResponse(
        parts=[TextPart(content="hi")],
        finish_reason="stop",
        usage=RequestUsage(input_tokens=input_tokens),
    )
    frontend = HeadlessFrontend()

    _emit_output_limit_diagnostics(response, deps, frontend, tracing.current_span())

    assert any("Auto-compaction paused" in s for s in frontend.statuses), frontend.statuses
