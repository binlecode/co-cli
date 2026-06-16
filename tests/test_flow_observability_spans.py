"""Span-tree emission via a real run_turn, plus the routing-wrapper error path.

The agent/model/tool spans are emitted on the seams co owns after the capability
removal: the agent span at the run call site (_execute_run), the chat
span in SurrogateRecoveryModel, and the tool span in _CallSeamToolset. This proves
the co tail / co trace tree is preserved at parity end-to-end.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent.toolset import _CallSeamToolset
from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    CoSessionState,
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


def _by_kind(records: list[dict], kind: str) -> list[dict]:
    return [r for r in records if r["kind"] == kind]


@pytest.mark.asyncio
async def test_tool_error_emits_error_span_and_clears_stack(tmp_path: Path) -> None:
    """A tool that raises produces a tool ERROR record and leaves the span stack empty."""
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    inner: FunctionToolset = FunctionToolset()

    async def boom() -> str:
        raise ValueError("tool exploded")

    inner.add_function(boom, requires_approval=False)
    routing = _CallSeamToolset(inner)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), run_step=1)
    tool = (await routing.get_tools(ctx))["boom"]

    with pytest.raises(ValueError, match="tool exploded"):
        await routing.call_tool("boom", {}, ctx, tool)

    assert tracing._SPAN_STACK.get() == (), "span stack must be empty after the error"
    error_recs = [r for r in _by_kind(_read_records(log), "tool") if r["status"] == "ERROR"]
    assert len(error_recs) == 1
    assert "tool exploded" in error_recs[0]["status_msg"]
