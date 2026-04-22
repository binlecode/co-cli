"""Turn-level regression tests for overflow routing and history safety.

Covers:
- Gemini-style overflow 400 bypasses reformulation, reaches overflow path
- Terminal overflow does not persist reformulation prompts in returned history
- Non-overflow malformed-tool-call 400 still uses reformulation (regression guard)
- HTTP 413 reaches the overflow recovery path
"""

import asyncio

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from tests._frontend import SilentFrontend
from tests._settings import make_settings

from co_cli.agent._core import build_agent
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps
from co_cli.tools.shell_backend import ShellBackend

_TURN_TIMEOUT_SECS: int = 10


def _make_deps() -> CoDeps:
    return CoDeps(shell=ShellBackend(), config=make_settings())


@pytest.mark.asyncio
async def test_gemini_overflow_400_routes_to_overflow_path() -> None:
    """Gemini-style overflow 400 reaches the overflow path, not tool reformulation.

    Under the old narrow predicate, "Request payload size exceeds the limit" was
    unrecognized and fell to the reformulation branch. The overflow path emits
    'Context overflow' status; the reformulation branch emits 'Tool call rejected'.
    """

    async def _raise_gemini_overflow(messages, agent_info):
        raise ModelHTTPError(
            status_code=400,
            model_name="test",
            body={"error": {"message": "Request payload size exceeds the limit"}},
        )
        yield

    deps = _make_deps()
    agent = build_agent(
        config=deps.config, model=FunctionModel(stream_function=_raise_gemini_overflow)
    )
    frontend = SilentFrontend()

    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert turn.outcome == "error"
    assert any("Context overflow" in s for s in frontend.statuses)
    assert not any("Tool call rejected" in s for s in frontend.statuses)


@pytest.mark.asyncio
async def test_terminal_overflow_no_reformulation_prompts_in_history() -> None:
    """Terminal overflow returns history with no injected reformulation UserPromptParts.

    Under the old code, a misclassified Gemini overflow 400 would append a
    'Your previous tool call was rejected' UserPromptPart before terminal failure,
    contaminating history for the next turn.
    """

    async def _raise_overflow(messages, agent_info):
        raise ModelHTTPError(
            status_code=400,
            model_name="test",
            body={"error": {"message": "Input token count exceeds the maximum"}},
        )
        yield

    deps = _make_deps()
    agent = build_agent(config=deps.config, model=FunctionModel(stream_function=_raise_overflow))

    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )

    assert turn.outcome == "error"
    for msg in turn.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    assert "previous tool call was rejected" not in part.content.lower(), (
                        f"Reformulation prompt leaked into history: {part.content!r}"
                    )


@pytest.mark.asyncio
async def test_malformed_400_still_uses_reformulation() -> None:
    """Non-overflow malformed-tool-call 400 still routes to the reformulation path.

    The new classifier must not steal descriptive 400s — 'bad tool call' carries
    no overflow evidence and must continue through reformulation.
    """
    call_count = [0]

    async def _400_then_text(messages, agent_info):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ModelHTTPError(status_code=400, model_name="test", body="bad tool call")
        yield "Understood, reformulating."

    deps = _make_deps()
    agent = build_agent(config=deps.config, model=FunctionModel(stream_function=_400_then_text))
    frontend = SilentFrontend()

    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert turn.outcome == "continue", "Non-overflow 400 must succeed after reformulation"
    assert any("Tool call rejected" in s for s in frontend.statuses)
    assert not any("Context overflow" in s for s in frontend.statuses)


@pytest.mark.asyncio
async def test_413_reaches_overflow_recovery_path() -> None:
    """HTTP 413 reaches the overflow recovery path as documented."""

    async def _raise_413(messages, agent_info):
        raise ModelHTTPError(status_code=413, model_name="test", body="request entity too large")
        yield

    deps = _make_deps()
    agent = build_agent(config=deps.config, model=FunctionModel(stream_function=_raise_413))
    frontend = SilentFrontend()

    async with asyncio.timeout(_TURN_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input="hello",
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert turn.outcome == "error"
    assert any("Context overflow" in s for s in frontend.statuses)
