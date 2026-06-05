"""Flow tests for token-usage capture, fork-sharing, and per-turn flush.

All ledger I/O is real (CO_HOME-overridden temp dir). Both capture chokepoints
are exercised with synthesized provider usage — no real LLM call needed to prove
coverage, keeping the flow deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from tests._settings import SETTINGS

from co_cli.context.orchestrate import TurnResult
from co_cli.daemons.dream._loop import _flush_daemon_usage
from co_cli.deps import CoDeps, CoSessionState, fork_deps
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.call import llm_call
from co_cli.main import _apply_command_outcome, _finalize_turn
from co_cli.observability.capability import ObservabilityCapability
from co_cli.session.usage import aggregate, record_usage
from co_cli.tools.shell_backend import ShellBackend


def _make_deps(tmp_path: Path) -> CoDeps:
    session_path = tmp_path / "sessions" / "2026-06-04T120000.000-abcd1234.jsonl"
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(session_path=session_path),
        sessions_dir=tmp_path / "sessions",
        usage_log_path=tmp_path / "usage.jsonl",
    )


def _ledger_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_after_model_request_hook_bumps_accumulator(tmp_path: Path) -> None:
    """ObservabilityCapability.after_model_request records the response's usage."""
    deps = _make_deps(tmp_path)
    response = ModelResponse(
        parts=[TextPart(content="hi")],
        model_name="fn",
        usage=RequestUsage(input_tokens=42, output_tokens=7),
    )
    ctx = SimpleNamespace(deps=deps)

    await ObservabilityCapability().after_model_request(
        ctx, request_context=None, response=response
    )

    assert deps.usage_accumulator.input_tokens == 42
    assert deps.usage_accumulator.output_tokens == 7


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
