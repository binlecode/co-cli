"""Tests for CoToolLifecycle.before_node_run — dedup duplicate ToolCallParts."""

import pytest
from pydantic_ai import CallToolsNode, ModelRequestNode, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.lifecycle import CoToolLifecycle
from co_cli.tools.shell_backend import ShellBackend


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
    )


def _ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage(), run_step=1)


def _call_tools_node(parts: list) -> CallToolsNode:
    return CallToolsNode(model_response=ModelResponse(parts=parts))


def _tool_call_parts_in(node: CallToolsNode) -> list[ToolCallPart]:
    return [p for p in node.model_response.parts if isinstance(p, ToolCallPart)]


@pytest.mark.asyncio
async def test_identical_tool_calls_collapsed_to_first():
    lc = CoToolLifecycle()
    ctx = _ctx(_make_deps())
    node = _call_tools_node(
        [
            ToolCallPart(tool_name="file_read", args={"path": "/a.txt"}, tool_call_id="c1"),
            ToolCallPart(tool_name="file_read", args={"path": "/a.txt"}, tool_call_id="c2"),
        ]
    )
    result = await lc.before_node_run(ctx, node=node)
    calls = _tool_call_parts_in(result)
    assert len(calls) == 1
    assert calls[0].tool_call_id == "c1"


@pytest.mark.asyncio
async def test_same_tool_distinct_args_both_preserved():
    lc = CoToolLifecycle()
    ctx = _ctx(_make_deps())
    node = _call_tools_node(
        [
            ToolCallPart(tool_name="file_read", args={"path": "/a.txt"}, tool_call_id="c1"),
            ToolCallPart(tool_name="file_read", args={"path": "/b.txt"}, tool_call_id="c2"),
        ]
    )
    result = await lc.before_node_run(ctx, node=node)
    calls = _tool_call_parts_in(result)
    assert [c.tool_call_id for c in calls] == ["c1", "c2"]


@pytest.mark.asyncio
async def test_distinct_tool_same_args_both_preserved():
    lc = CoToolLifecycle()
    ctx = _ctx(_make_deps())
    node = _call_tools_node(
        [
            ToolCallPart(tool_name="file_read", args={"path": "/a.txt"}, tool_call_id="c1"),
            ToolCallPart(tool_name="file_write", args={"path": "/a.txt"}, tool_call_id="c2"),
        ]
    )
    result = await lc.before_node_run(ctx, node=node)
    calls = _tool_call_parts_in(result)
    assert [c.tool_name for c in calls] == ["file_read", "file_write"]


@pytest.mark.asyncio
async def test_text_parts_preserved_in_order_when_dedup_fires():
    lc = CoToolLifecycle()
    ctx = _ctx(_make_deps())
    node = _call_tools_node(
        [
            TextPart(content="thinking..."),
            ToolCallPart(tool_name="file_read", args={"path": "/a.txt"}, tool_call_id="c1"),
            ToolCallPart(tool_name="file_read", args={"path": "/a.txt"}, tool_call_id="c2"),
            TextPart(content="done"),
        ]
    )
    result = await lc.before_node_run(ctx, node=node)
    parts = result.model_response.parts
    assert [type(p).__name__ for p in parts] == ["TextPart", "ToolCallPart", "TextPart"]
    assert parts[0].content == "thinking..."
    assert parts[1].tool_call_id == "c1"
    assert parts[2].content == "done"


@pytest.mark.asyncio
async def test_non_call_tools_node_returned_unchanged():
    lc = CoToolLifecycle()
    ctx = _ctx(_make_deps())
    node = ModelRequestNode(request=ModelRequest(parts=[UserPromptPart(content="hi")]))
    result = await lc.before_node_run(ctx, node=node)
    assert result is node


@pytest.mark.asyncio
async def test_string_args_dedup_by_byte_identity():
    """Raw string args (pre-validation) dedup by byte-identical match."""
    lc = CoToolLifecycle()
    ctx = _ctx(_make_deps())
    node = _call_tools_node(
        [
            ToolCallPart(tool_name="shell_exec", args='{"cmd": "ls"}', tool_call_id="c1"),
            ToolCallPart(tool_name="shell_exec", args='{"cmd": "ls"}', tool_call_id="c2"),
        ]
    )
    result = await lc.before_node_run(ctx, node=node)
    calls = _tool_call_parts_in(result)
    assert len(calls) == 1
    assert calls[0].tool_call_id == "c1"
