"""Consolidated E2E tests for test_flow_tool_calling_functional."""

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.result import DeferredToolRequests
from tests._frontend import SilentFrontend
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import build_tool_registry
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)
_TOOL_REG = build_tool_registry(_CONFIG_NO_MCP)
_AGENT_NOREASON = Agent(
    _LLM_MODEL.model,
    deps_type=CoDeps,
    model_settings=_LLM_MODEL.settings_noreason,
    retries=_CONFIG_NO_MCP.tool_retries,
    output_type=[str, DeferredToolRequests],
    toolsets=[_TOOL_REG.toolset],
)


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
    )


@pytest.mark.asyncio
async def test_refusal_no_tool_for_simple_math():
    """Agent should not invoke any tool for a purely conversational / math prompt."""
    deps = _make_deps()
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        turn = await run_turn(
            agent=_AGENT_NOREASON,
            user_input="What is 17 times 23?",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )
    for msg in turn.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                assert not isinstance(part, ToolCallPart), (
                    f"Expected no tool call, got {part.tool_name!r}"
                )


@pytest.mark.asyncio
async def test_tool_selection_shell_git_status():
    """Agent must correctly route a request to execute a bash command to the shell tool."""
    agent = _AGENT_NOREASON
    deps = _make_deps()
    frontend = SilentFrontend(approval_response="y")

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        turn = await run_turn(
            agent=agent,
            user_input="Use the shell tool to execute: git status. Do NOT describe what you would do - call the tool now.",
            deps=deps,
            message_history=[],
            frontend=frontend,
        )
    tool_name = None
    args = None
    for msg in turn.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tool_name = part.tool_name
                    args = part.args_as_dict()
                    break
        if tool_name:
            break

    assert tool_name == "bash" or tool_name == "shell", (
        f"Expected shell tool invocation, got {tool_name}"
    )
    assert "git status" in str(args)
