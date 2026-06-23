"""Tests for safety_prompt_text: doom-loop and shell-reflection streak counting.

Regression coverage for the part-ordering bug — both streak counters scan
messages newest-first, so a parallel batch within one message must be read
newest-first too. A return batch ``[err, err, err, ok]`` (ok most recent) must
report a streak of 0, not 3.
"""

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.context.prompt_text import safety_prompt_text
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend

_DEPS = CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP, session=CoSessionState())

_DOOM_MARKER = "repeating the same tool call"
_REFLECTION_MARKER = "Shell reflection limit reached"


def _ctx(messages: list[ModelMessage]) -> RunContext[CoDeps]:
    ctx: RunContext[CoDeps] = RunContext(deps=_DEPS, model=None, usage=RunUsage())
    ctx.messages = messages
    return ctx


def _shell_call(call_id: str) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name="shell_exec", args={"cmd": "ls"}, tool_call_id=call_id)]
    )


def _shell_err(call_id: str) -> ToolReturnPart:
    return ToolReturnPart(
        tool_name="shell_exec",
        content="error: command failed",
        tool_call_id=call_id,
        metadata={"error": True},
    )


def _shell_ok(call_id: str) -> ToolReturnPart:
    return ToolReturnPart(tool_name="shell_exec", content="ok", tool_call_id=call_id)


def test_shell_batch_ending_in_success_does_not_warn():
    """A return batch [err, err, err, ok] with ok most-recent is a streak of 0."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="run it")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="shell_exec", args={"cmd": "ls"}, tool_call_id=f"c{i}")
                for i in range(4)
            ]
        ),
        ModelRequest(
            parts=[
                _shell_err("c0"),
                _shell_err("c1"),
                _shell_err("c2"),
                _shell_ok("c3"),
            ]
        ),
    ]
    assert _REFLECTION_MARKER not in safety_prompt_text(_ctx(messages))


def test_shell_batch_ending_in_errors_warns():
    """A return batch [ok, err, err, err] with errors most-recent crosses the cap."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="run it")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="shell_exec", args={"cmd": "ls"}, tool_call_id=f"c{i}")
                for i in range(4)
            ]
        ),
        ModelRequest(
            parts=[
                _shell_ok("c0"),
                _shell_err("c1"),
                _shell_err("c2"),
                _shell_err("c3"),
            ]
        ),
    ]
    assert _REFLECTION_MARKER in safety_prompt_text(_ctx(messages))


def test_doom_batch_ending_in_different_call_does_not_warn():
    """A call batch [A, A, A, B] with B most-recent is a streak of 1."""
    same = {"cmd": "ls"}
    other = {"cmd": "pwd"}
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="go")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="shell_exec", args=same, tool_call_id="a0"),
                ToolCallPart(tool_name="shell_exec", args=same, tool_call_id="a1"),
                ToolCallPart(tool_name="shell_exec", args=same, tool_call_id="a2"),
                ToolCallPart(tool_name="shell_exec", args=other, tool_call_id="b0"),
            ]
        ),
    ]
    assert _DOOM_MARKER not in safety_prompt_text(_ctx(messages))


def test_doom_repeated_calls_warns():
    """Three identical most-recent calls cross the doom threshold."""
    same = {"cmd": "ls"}
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="go")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="shell_exec", args=same, tool_call_id="a0"),
                ToolCallPart(tool_name="shell_exec", args=same, tool_call_id="a1"),
                ToolCallPart(tool_name="shell_exec", args=same, tool_call_id="a2"),
            ]
        ),
    ]
    assert _DOOM_MARKER in safety_prompt_text(_ctx(messages))
