"""Tests for provider_error span events on terminal ModelHTTPError paths in run_turn().

Covers:
- terminal 429 adds provider_error event with correct attributes
- error body longer than 500 chars is truncated to exactly 500
- HTTP 400 reformulation retry (budget > 0) does NOT add provider_error event
- budget-exhausted 400 (budget == 0) DOES add provider_error event
"""

import asyncio
import json
import sqlite3
import time

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import FunctionModel
from tests._frontend import SilentFrontend
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.config._core import LOGS_DB
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend

# run_turn() internally wraps each segment with asyncio.timeout(60s). This outer
# timeout is a belt-and-suspenders guard; FunctionModel has no real I/O overhead.
_TURN_TIMEOUT_SECS: int = 10


def _make_deps() -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=make_settings())


def _get_co_turn_events_after(start_ns: int) -> list[dict]:
    """Return all events from co.turn spans whose start_time >= start_ns."""
    if not LOGS_DB.exists():
        return []
    with sqlite3.connect(str(LOGS_DB)) as conn:
        rows = conn.execute(
            "SELECT events FROM spans WHERE name = 'co.turn' AND start_time >= ?",
            (start_ns,),
        ).fetchall()
    events: list[dict] = []
    for (events_json,) in rows:
        if events_json:
            try:
                for evt in json.loads(events_json):
                    events.append(evt)
            except json.JSONDecodeError:
                pass
    return events


@pytest.mark.asyncio
async def test_terminal_429_records_provider_error_event() -> None:
    """A terminal 429 ModelHTTPError adds a provider_error event to the co.turn span."""

    async def _raise_429(messages, agent_info):
        raise ModelHTTPError(status_code=429, model_name="test", body="rate limit exceeded")
        yield  # makes this an async generator

    deps = _make_deps()
    agent = build_agent(config=deps.config, model=FunctionModel(stream_function=_raise_429))

    before_ns = time.time_ns()
    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    assert turn.outcome == "error"
    events = _get_co_turn_events_after(before_ns)
    provider_errors = [e for e in events if e.get("name") == "provider_error"]
    assert len(provider_errors) == 1
    assert provider_errors[0]["attributes"]["http.status_code"] == 429
    assert provider_errors[0]["attributes"]["error.body"] == "rate limit exceeded"


@pytest.mark.asyncio
async def test_provider_error_body_truncated_to_500_chars() -> None:
    """An error body longer than 500 characters is stored truncated to exactly 500."""
    long_body = "x" * 600

    async def _raise_long_body(messages, agent_info):
        raise ModelHTTPError(status_code=429, model_name="test", body=long_body)
        yield

    deps = _make_deps()
    agent = build_agent(config=deps.config, model=FunctionModel(stream_function=_raise_long_body))

    before_ns = time.time_ns()
    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    events = _get_co_turn_events_after(before_ns)
    provider_errors = [e for e in events if e.get("name") == "provider_error"]
    assert len(provider_errors) == 1
    assert len(provider_errors[0]["attributes"]["error.body"]) == 500


@pytest.mark.asyncio
async def test_400_reformulation_retry_no_provider_error_event() -> None:
    """HTTP 400 reformulation retry (budget > 0, continue) does NOT add a provider_error event."""
    call_count = [0]

    async def _400_then_text(messages, agent_info):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ModelHTTPError(status_code=400, model_name="test", body="bad tool call")
        yield "I understand, proceeding with correction."

    deps = _make_deps()
    agent = build_agent(config=deps.config, model=FunctionModel(stream_function=_400_then_text))

    before_ns = time.time_ns()
    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    assert turn.outcome == "continue", "Turn must succeed after reformulation retry"
    events = _get_co_turn_events_after(before_ns)
    provider_errors = [e for e in events if e.get("name") == "provider_error"]
    assert provider_errors == [], "Reformulation retry must not add a provider_error event"


@pytest.mark.asyncio
async def test_budget_exhausted_400_records_provider_error_event() -> None:
    """Budget-exhausted HTTP 400 (tool_reformat_budget == 0) adds a provider_error event."""

    async def _always_raise_400(messages, agent_info):
        raise ModelHTTPError(status_code=400, model_name="test", body="bad tool call")
        yield

    deps = _make_deps()
    agent = build_agent(config=deps.config, model=FunctionModel(stream_function=_always_raise_400))

    before_ns = time.time_ns()
    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    assert turn.outcome == "error"
    events = _get_co_turn_events_after(before_ns)
    provider_errors = [e for e in events if e.get("name") == "provider_error"]
    assert len(provider_errors) == 1
    assert provider_errors[0]["attributes"]["http.status_code"] == 400
