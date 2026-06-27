"""Write-capable delegation gated end-to-end on the owned loop (Phase 3.5).

The behavioral gate: the orchestrator delegates a write subtask through the owned turn loop;
the child's file_write surfaces as an approval prompt on the parent's scripted frontend.

  - deny    → the side effect does NOT happen (verified on disk) and the delegate result is
              still a summary (the turn continues);
  - approve → the side effect happens, AND context isolation holds (the parent history carries
              the delegate summary ToolReturnPart but none of the child's file_write result);
  - headless via the runtime.frontend-is-None path → the child's collector still runs and
    auto-denies, no side effect (G1-1: a write-capable child with no frontend never acts
    unprompted — uses a file_write subtask, gated by the collector alone, not shell whose
    in-body raise would mask a regression). Exercised at the delegate_to_child boundary,
    where runtime.frontend is genuinely None (run_turn_owned always sets it at turn entry).

Real-LLM tests skip unless Ollama is configured; the model is warmed outside the timeout.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
from co_cli.agent.delegation import delegate_to_child
from co_cli.agent.loop import run_turn_owned
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_NATIVE, _CATALOG = build_native_toolset()


def _make_deps(workspace: Path) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=build_model(_CONFIG_NO_MCP.llm),
        toolset=assemble_routing_toolset(_NATIVE, []),
        tool_catalog=_CATALOG,
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        workspace_dir=workspace,
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
    )


def _delegate_write_ask(target: Path) -> str:
    return (
        f"Use the delegate tool to create a file at {target} containing the word `done`. "
        "Pass the sub-agent a complete instruction to use file_write for that file."
    )


def _child_write_task(target: Path) -> str:
    return (
        f"Use the file_write tool to create a file at {target} with the content `done`. "
        "Then summarize what you did."
    )


def _tool_return_names(messages: list) -> list[str]:
    names: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    names.append(part.tool_name)
    return names


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM delegation approval needs Ollama"
)
@pytest.mark.asyncio
async def test_delegated_write_denied_blocks_side_effect(tmp_path: Path) -> None:
    """A scripted denial blocks the child's write on disk; the delegate result is still a
    summary and the parent turn continues."""
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = tmp_path / "delegated.txt"
    deps = _make_deps(tmp_path)
    frontend = HeadlessFrontend(approval_response="n")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 3):
        turn = await run_turn_owned(
            user_input=_delegate_write_ask(target),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert frontend.approval_calls, "the child's write surfaced on the parent frontend"
    assert not target.exists(), "the denied write did not create the file"
    assert turn.outcome == "continue", "the turn continues after the child's denial"
    assert "delegate" in _tool_return_names(turn.messages), "delegate returned a summary"


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM delegation approval needs Ollama"
)
@pytest.mark.asyncio
async def test_delegated_write_approved_executes_and_isolates(tmp_path: Path) -> None:
    """A scripted approval lets the child's write happen on disk, and the child's intermediate
    file_write result never enters the parent history — only the delegate summary does."""
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = tmp_path / "delegated.txt"
    deps = _make_deps(tmp_path)
    frontend = HeadlessFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 3):
        turn = await run_turn_owned(
            user_input=_delegate_write_ask(target),
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert frontend.approval_calls, "the child's write surfaced on the parent frontend"
    assert target.exists(), "the approved write created the file"
    return_names = _tool_return_names(turn.messages)
    assert "delegate" in return_names, "the delegate summary entered parent history"
    assert "file_write" not in return_names, "the child's file_write result stayed isolated"


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM delegation approval needs Ollama"
)
@pytest.mark.asyncio
async def test_delegated_write_headless_parent_auto_denies(tmp_path: Path) -> None:
    """G1-1: with the parent's runtime.frontend None, the child's collector still runs and
    auto-denies — a write-capable child never acts unprompted.

    delegate_to_child reads parent_deps.runtime.frontend (here unset → None) and passes
    propagate_approvals=True with frontend=None, so the child's collector auto-denies the
    file_write. The file's absence is the side-effect proof; child token spend proves the
    child actually ran and attempted the gated work (not a vacuous no-op or depth refusal).
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = tmp_path / "delegated.txt"
    deps = _make_deps(tmp_path)
    assert deps.runtime.frontend is None, "parent runs headless (no frontend on runtime)"

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        summary = await delegate_to_child(deps, _child_write_task(target))

    assert not target.exists(), "headless child auto-denied its write — no side effect"
    assert deps.usage_accumulator.output_tokens > 0, "the child actually ran (not a no-op)"
    assert summary, "delegate_to_child still returns a summary"
