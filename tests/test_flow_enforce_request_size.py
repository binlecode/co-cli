"""Tests for L2 aggregate request-budget enforcement.

Exercises the post-tool-exec hook surface: ``CoToolLifecycle.after_node_run``
calls ``_enforce_request_budget`` on the upcoming ``ModelRequest``
(``ModelRequest.parts`` of the next ``ModelRequestNode``). Tests construct a
fake ``CallToolsNode`` and a fake ``ModelRequestNode`` with the test's
``ToolReturnPart``s, then assert against the post-mutation ``result.request.parts``.
"""

from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import CallToolsNode, ModelRequestNode, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage
from pydantic_graph.nodes import End
from tests._settings import SETTINGS_NO_MCP

from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.lifecycle import CoToolLifecycle
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG


def _make_deps(
    tmp_path: Path,
    *,
    threshold_tokens: int,
    model_max_ctx: int = 131_072,
) -> CoDeps:
    """Build a minimal CoDeps suitable for request-budget hook tests."""
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path,
        model_max_ctx=model_max_ctx,
        request_aggregate_threshold_tokens=threshold_tokens,
    )


def _ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _batch(*call_id_content: tuple[str, str], tool_name: str = "shell") -> list[ToolReturnPart]:
    """Build a batch of ToolReturnParts — the parts of one ModelRequestNode.request."""
    return [
        ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=cid)
        for cid, content in call_id_content
    ]


def _calltools_node(*call_ids: str, tool_name: str = "shell") -> CallToolsNode:
    """Construct a CallToolsNode whose model_response carries N ToolCallParts."""
    return CallToolsNode(
        model_response=ModelResponse(
            parts=[
                ToolCallPart(tool_name=tool_name, args={}, tool_call_id=cid) for cid in call_ids
            ]
        )
    )


def _model_request_node(parts: list[ToolReturnPart]) -> ModelRequestNode:
    """Construct a ModelRequestNode whose request.parts is the batch under test."""
    return ModelRequestNode(request=ModelRequest(parts=parts))


def _collect(node: ModelRequestNode) -> dict[str, str]:
    return {
        p.tool_call_id: p.content
        for p in node.request.parts
        if isinstance(p, ToolReturnPart) and isinstance(p.content, str)
    }


@pytest.mark.asyncio
async def test_below_threshold_no_spill(tmp_path: Path):
    """Two small returns in one batch must pass through unchanged."""
    parts = _batch(("tc1", "a" * 3_000), ("tc2", "b" * 3_000))
    node = _calltools_node("tc1", "tc2")
    result = _model_request_node(list(parts))
    deps = _make_deps(tmp_path, threshold_tokens=50_000)

    out = await CoToolLifecycle().after_node_run(_ctx(deps), node=node, result=result)

    assert out is result
    assert out.request.parts == parts
    for content in _collect(out).values():
        assert PERSISTED_OUTPUT_TAG not in content


@pytest.mark.asyncio
async def test_force_spill_largest_first(tmp_path: Path):
    """Three returns in one batch; over-budget aggregate must spill largest-first.

    16K-char (4K tokens), 24K-char (6K tokens), 32K-char (8K tokens) =
    18K tokens total over a 5K-token threshold. Largest two must spill;
    smallest stays intact.
    """
    content_small = "s" * 16_000
    content_mid = "m" * 24_000
    content_large = "l" * 32_000

    node = _calltools_node("tc_small", "tc_mid", "tc_large")
    result = _model_request_node(
        _batch(("tc_small", content_small), ("tc_mid", content_mid), ("tc_large", content_large))
    )
    deps = _make_deps(tmp_path, threshold_tokens=5_000)

    out = await CoToolLifecycle().after_node_run(_ctx(deps), node=node, result=result)

    returns = _collect(out)
    assert returns["tc_small"] == content_small, "smallest return must remain unspilled"
    assert PERSISTED_OUTPUT_TAG in returns["tc_mid"], "mid return must be spilled"
    assert PERSISTED_OUTPUT_TAG in returns["tc_large"], "large return must be spilled"
    assert sum(1 for c in returns.values() if PERSISTED_OUTPUT_TAG in c) == 2

    large_stub = returns["tc_large"]
    assert "32,000 chars" in large_stub or "32000" in large_stub.replace(",", "")


@pytest.mark.asyncio
async def test_all_spilled_bail_out(tmp_path: Path):
    """When all candidates already start with PERSISTED_OUTPUT_TAG, bail out.

    runtime.current_request_aggregate_tokens_after_spill must remain None.
    """
    stub = (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"This tool result was too large (50000 chars, 48.8 KB).\n"
        f"tool: shell\n"
        f"file: /tmp/abc123.txt\n"
        f"To read the full output, call file_read with the path above and use "
        f"start_line/end_line to page through it in chunks.\n"
        f"preview:\n{'x' * 800}\n"
        f"</persisted-output>"
    )

    parts = _batch(("tc1", stub), ("tc2", stub))
    node = _calltools_node("tc1", "tc2")
    result = _model_request_node(list(parts))
    deps = _make_deps(tmp_path, threshold_tokens=100)

    out = await CoToolLifecycle().after_node_run(_ctx(deps), node=node, result=result)

    assert out.request.parts == parts
    assert deps.runtime.current_request_aggregate_tokens_after_spill is None


@pytest.mark.asyncio
async def test_uses_cached_threshold(tmp_path: Path):
    """Hook must read deps.request_aggregate_threshold_tokens directly.

    52K-char return = 13K tokens > 12K threshold → spill fires.
    """
    content = "t" * 52_000
    node = _calltools_node("tc1")
    result = _model_request_node(_batch(("tc1", content)))
    deps = _make_deps(tmp_path, threshold_tokens=12_000)

    out = await CoToolLifecycle().after_node_run(_ctx(deps), node=node, result=result)

    returns = _collect(out)
    assert len(returns) == 1
    assert PERSISTED_OUTPUT_TAG in returns["tc1"], (
        "13K-token aggregate must trigger spill at threshold=12K"
    )
    assert deps.runtime.current_request_aggregate_tokens_after_spill is not None


@pytest.mark.asyncio
async def test_final_result_path_is_noop(tmp_path: Path):
    """When the model emits a final-result tool call, ``CallToolsNode.run`` returns
    ``End[FinalResult]`` instead of a ``ModelRequestNode``. The hook must
    short-circuit: no enforcement, no mutation, no L2 span. The L0 tool-call-limit
    span still fires (sibling, not nested).
    """
    node = _calltools_node("tc_final")
    end_result: Any = End(data="final answer")
    deps = _make_deps(tmp_path, threshold_tokens=100)

    out = await CoToolLifecycle().after_node_run(_ctx(deps), node=node, result=end_result)

    assert out is end_result
    assert deps.runtime.current_request_aggregate_tokens_after_spill is None


@pytest.mark.asyncio
async def test_non_calltools_node_passthrough(tmp_path: Path):
    """Hook must short-circuit when the just-finished node is not CallToolsNode —
    no L0 span, no L2 enforcement. Uses ModelRequestNode as a real non-CTN AgentNode.
    """
    parts = _batch(("tc1", "x" * 100_000))
    non_ctn_node = _model_request_node([])
    result = _model_request_node(list(parts))
    deps = _make_deps(tmp_path, threshold_tokens=100)

    out = await CoToolLifecycle().after_node_run(_ctx(deps), node=non_ctn_node, result=result)

    assert out is result
    assert out.request.parts == parts
    assert deps.runtime.current_request_aggregate_tokens_after_spill is None
