"""Tests for HTTP 400 tool-call reformulation recovery in run_turn().

Production path: co_cli/agent/orchestrate.py — the ModelHTTPError(400) branch,
_apply_400_reformulation(), and the reformulation_clean_history reconstruction.

A provider 400 (malformed tool-call JSON) raises out of _execute_run before the
run's messages are captured, so the failed run's all_messages() is lost. The
recovery must (1) preserve the original user turn in the final transcript and
(2) NOT persist the synthetic reformulation nudge as a user turn.

Driven with a fake model so the 400-then-success path is deterministic; the real
run_turn / SessionAgent / new_messages() / all_messages() code runs unchanged.
"""

from collections.abc import AsyncIterator

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.build import build_orchestrator
from co_cli.agent.core import build_native_toolset
from co_cli.agent.orchestrate import run_turn
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import LlmModel
from co_cli.tools.shell_backend import ShellBackend

_TOOLSET, _TOOL_INDEX = build_native_toolset()

_NUDGE_MARKER = "rejected by the model provider"


def _fail_then_succeed_model(fail_count: int) -> FunctionModel:
    """A model that raises HTTP 400 on its first ``fail_count`` calls, then answers."""
    state = {"calls": 0}

    async def stream_fn(messages: list, info: AgentInfo) -> AsyncIterator[str]:
        state["calls"] += 1
        if state["calls"] <= fail_count:
            raise ModelHTTPError(
                status_code=400,
                model_name="fn",
                body={"error": "invalid tool call arguments"},
            )
        yield "All set."

    return FunctionModel(stream_function=stream_fn)


def _make_deps(fail_count: int) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=LlmModel(
            model=_fail_then_succeed_model(fail_count),
            settings=SETTINGS_NO_MCP.llm.noreason_model_settings(),
            settings_noreason=SETTINGS_NO_MCP.llm.noreason_model_settings(),
        ),
        toolset=_TOOLSET,
        tool_catalog=_TOOL_INDEX,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )


def _user_prompt_texts(messages: list) -> list[str]:
    return [
        part.content
        for msg in messages
        for part in getattr(msg, "parts", [])
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]


@pytest.mark.asyncio
async def test_single_reformulation_preserves_user_input_and_strips_nudge() -> None:
    """One 400 then success: user turn survives, synthetic nudge is absent."""
    deps = _make_deps(fail_count=1)
    agent = build_orchestrator(ORCHESTRATOR_SPEC, deps)
    user_input = "list the files in this repo"

    turn = await run_turn(
        agent=agent,
        user_input=user_input,
        deps=deps,
        message_history=[],
        model_settings=None,
        frontend=SilentFrontend(),
    )

    assert turn.outcome == "continue"
    assert user_input in _user_prompt_texts(turn.messages)
    assert not any(_NUDGE_MARKER in text for text in _user_prompt_texts(turn.messages))


@pytest.mark.asyncio
async def test_double_reformulation_preserves_user_input_and_strips_nudge() -> None:
    """Two 400s (full reformulation budget) then success: same guarantees hold."""
    deps = _make_deps(fail_count=2)
    agent = build_orchestrator(ORCHESTRATOR_SPEC, deps)
    user_input = "summarize the README"

    turn = await run_turn(
        agent=agent,
        user_input=user_input,
        deps=deps,
        message_history=[],
        model_settings=None,
        frontend=SilentFrontend(),
    )

    assert turn.outcome == "continue"
    assert user_input in _user_prompt_texts(turn.messages)
    assert not any(_NUDGE_MARKER in text for text in _user_prompt_texts(turn.messages))
