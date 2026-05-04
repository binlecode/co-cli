"""Consolidated E2E tests for test_flow_tool_calling_functional."""

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import build_agent, build_tool_registry
from co_cli.context.orchestrate import run_turn
from co_cli.deps import ApprovalKindEnum, CoDeps, CoSessionState, SessionApprovalRule
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import LlmModel, build_model
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)
_TOOL_REG = build_tool_registry(_CONFIG_NO_MCP)
# Production agent via build_agent() with noreason settings per test policy for tool-calling tests.
_LLM_NOREASON = LlmModel(
    model=_LLM_MODEL.model,
    settings=_LLM_MODEL.settings_noreason,
    settings_noreason=_LLM_MODEL.settings_noreason,
    context_window=_LLM_MODEL.context_window,
)
_AGENT = build_agent(config=_CONFIG_NO_MCP, model=_LLM_NOREASON, tool_registry=_TOOL_REG)


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
            agent=_AGENT,
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
    agent = _AGENT
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


@pytest.mark.asyncio
async def test_denied_tool_does_not_execute(tmp_path: Path) -> None:
    """Denied tool must not execute — file must not be created after user denies.

    Failure mode: ToolDenied not wired into the SDK resume path → tool runs despite
    denial → unauthorized writes or shell commands execute silently.
    """
    denied_path = tmp_path / "denied.txt"
    deps = _make_deps()
    frontend = SilentFrontend(approval_response="n")

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        result = await run_turn(
            agent=_AGENT,
            user_input=(
                f"Use the file_write tool to create the file '{denied_path}' "
                "with content 'hello'. Call the tool now."
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert result.outcome == "continue"
    assert not denied_path.exists(), f"Denied file must not be created, but {denied_path} exists"


@pytest.mark.asyncio
async def test_auto_approval_skips_prompt_for_remembered_session_rule() -> None:
    """Session approval rule must auto-approve matching tool calls without prompting.

    Pre-seeds a SHELL rule for 'git'. Prompts for 'git remote' (requires approval,
    not in safe_commands). Auto-approval must handle the deferred call without
    invoking prompt_approval on the frontend.

    Failure mode: is_auto_approved mis-matches the session rule → user is re-prompted
    every turn for a tool they already approved, breaking the 'always' contract.
    """
    deps = _make_deps()
    deps.session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="git")
    )
    frontend = SilentFrontend(approval_response="n")  # fail-safe: deny anything that leaks

    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        result = await run_turn(
            agent=_AGENT,
            user_input=(
                "Use the shell tool to execute: git remote. "
                "Do NOT describe what you would do - call the tool now."
            ),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert result.outcome == "continue"
    assert len(frontend.approval_calls) == 0, (
        f"prompt_approval must not be called when a session rule matches, "
        f"but got {len(frontend.approval_calls)} call(s): {frontend.approval_calls}"
    )
