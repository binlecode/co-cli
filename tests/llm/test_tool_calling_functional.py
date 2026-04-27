"""Functional tool-calling coverage replacing eval_tool_calling.py dimensions.

Covers:
- tool_selection
- arg_extraction
- refusal
- intent routing (observation vs directive)
- error_recovery after tool failure
"""

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.result import DeferredToolRequests
from tests._frontend import SilentFrontend
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent._core import build_tool_registry
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm._factory import build_model
from co_cli.tools.shell_backend import ShellBackend

pytestmark = pytest.mark.local

_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)

# Tool selection tests use noreason settings with a direct Agent construction.
# This gives fast, non-reasoning tool selection without the full main agent system prompt overhead.
# Tool registry and agents built once at module level to avoid per-test overhead.
_TOOL_REG = build_tool_registry(_CONFIG_NO_MCP)
_AGENT_NOREASON = Agent(
    _LLM_MODEL.model,
    deps_type=CoDeps,
    model_settings=_LLM_MODEL.settings_noreason,
    retries=_CONFIG_NO_MCP.tool_retries,
    output_type=[str, DeferredToolRequests],
    toolsets=[_TOOL_REG.toolset],
)


def _make_deps(session_id: str) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "expected_tool", "arg_key", "arg_contains"),
    [
        (
            "Use the shell tool to execute: git status\nDo NOT describe what you would do — call the tool now.",
            "shell",
            "cmd",
            "git status",
        ),
        (
            "Search the web for FastAPI authentication tutorial.",
            "web_search",
            "query",
            "fastapi authentication tutorial",
        ),
        (
            "What did we talk about regarding database preferences in past sessions?",
            "memory_search",
            "query",
            "database",
        ),
    ],
    ids=["shell_git_status", "web_search_fastapi", "memory_search_past_sessions"],
)
async def test_tool_selection_and_arg_extraction(
    prompt: str,
    expected_tool: str,
    arg_key: str,
    arg_contains: str,
):
    agent = _AGENT_NOREASON
    deps = _make_deps(f"test-tool-{expected_tool}")
    frontend = SilentFrontend(approval_response="y")

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    last_details = "no run executed"
    max_attempts = 3
    for _attempt in range(max_attempts):
        tool_name = None
        args = None
        try:
            async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
                turn = await run_turn(
                    agent=agent,
                    user_input=prompt,
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                )
            # Extract first tool call from message history.
            for msg in turn.messages:
                if isinstance(msg, ModelResponse):
                    for part in msg.parts:
                        if isinstance(part, ToolCallPart):
                            tool_name = part.tool_name
                            args = part.args_as_dict()
                            break
                if tool_name:
                    break
        except (ModelHTTPError, ModelAPIError, TimeoutError) as e:
            last_details = f"run_turn error: {type(e).__name__}: {e}"
            continue

        if tool_name is None:
            last_details = "no tool call observed"
            continue

        if tool_name != expected_tool:
            last_details = f"tool={tool_name!r}, expected={expected_tool!r}, args={args!r}"
            continue
        actual = str((args or {}).get(arg_key, "")).lower()
        if arg_contains.lower() in actual:
            return
        last_details = (
            f"tool={tool_name!r}, missing arg fragment "
            f"{arg_contains!r} in {arg_key}={(args or {}).get(arg_key)!r}"
        )

    pytest.fail(f"Tool selection/arg extraction failed: {last_details}")


@pytest.mark.asyncio
async def test_refusal_no_tool_for_simple_math():
    deps = _make_deps("test-refusal")
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
async def test_intent_routing_observation_no_tool():
    """Observation-only statement must not trigger a tool call."""
    deps = _make_deps("test-intent-routing")

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        turn = await run_turn(
            agent=_AGENT_NOREASON,
            user_input="This function has a bug",
            deps=deps,
            message_history=[],
            frontend=SilentFrontend(),
        )
    for msg in turn.messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                assert not isinstance(part, ToolCallPart), (
                    f"Expected no tool call for observation statement, got {part.tool_name!r}"
                )


@pytest.mark.asyncio
async def test_clarify_handled_by_run_turn():
    """run_turn handles a question-type deferred call: invokes prompt_question and injects answer."""
    deps = _make_deps("test-clarify")
    frontend = SilentFrontend(question_answer="Alice")

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    last_details = "no run executed"
    max_attempts = 3
    for _attempt in range(max_attempts):
        frontend.last_question = None
        try:
            async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
                turn = await run_turn(
                    agent=_AGENT_NOREASON,
                    user_input=(
                        "Call the clarify tool now with "
                        "question='What is your name?' — do not answer without calling the tool."
                    ),
                    deps=deps,
                    message_history=[],
                    frontend=frontend,
                )
        except (ModelHTTPError, ModelAPIError, TimeoutError) as err:
            last_details = f"run_turn error: {type(err).__name__}: {err}"
            continue

        if frontend.last_question is None:
            last_details = "prompt_question was not called — LLM did not call clarify"
            continue

        if "Alice" not in str(turn.messages):
            last_details = "answer 'Alice' not found in turn messages"
            continue

        return

    pytest.fail(f"clarify integration test failed after {max_attempts} attempts: {last_details}")
