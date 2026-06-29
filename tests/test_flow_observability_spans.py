"""Span emission on the owned tool-dispatch error path.

The ``co.tool.*`` span is emitted by ``dispatch_tools`` (``_execute_one``) after the
legacy graph wrapper-toolset removal. This proves a raising tool body closes its tool span
ERROR and leaves the span stack empty — the co tail / co trace tree stays consistent even
when a tool explodes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import FunctionToolset
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.dispatch import dispatch_tools
from co_cli.agent.turn_state import ToolCapState
from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
)
from co_cli.observability import tracing
from co_cli.tools.shell_backend import ShellBackend


@pytest.fixture(autouse=True)
def _reset_tracing() -> None:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    tracing._COMPILED_PATTERNS = []
    tracing._SESSION_ID.set(None)
    tracing._TRACE_ID.set(None)
    tracing._SPAN_STACK.set(())


def _read_records(log_path: Path) -> list[dict]:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in logger.handlers:
        handler.flush()
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_tool_error_emits_error_span_and_clears_stack(tmp_path: Path) -> None:
    """A tool that raises produces a tool ERROR record and leaves the span stack empty."""
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    inner: FunctionToolset = FunctionToolset()

    async def boom() -> str:
        raise ValueError("tool exploded")

    inner.add_function(boom, requires_approval=False)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path / "tool-results",
        tool_catalog={
            "boom": ToolInfo(
                name="boom",
                description="test",
                is_approval_required=False,
                source=ToolSourceEnum.NATIVE,
                visibility=VisibilityPolicyEnum.ALWAYS,
                is_concurrent_safe=True,
            )
        },
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )
    deps.toolset = inner

    with pytest.raises(ValueError, match="tool exploded"):
        await dispatch_tools(
            [ToolCallPart(tool_name="boom", args={}, tool_call_id="c1")],
            deps,
            cap_state=ToolCapState(),
        )

    assert tracing._SPAN_STACK.get() == (), "span stack must be empty after the error"
    error_recs = [r for r in _read_records(log) if r["kind"] == "tool" and r["status"] == "ERROR"]
    assert len(error_recs) == 1
    assert "tool exploded" in error_recs[0]["status_msg"]
