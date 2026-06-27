"""Full-surface delegation gated end-to-end on the owned loop (Phase 3.6).

The behavioral gate for the visibility-model delegated agent — the orchestrator's full
surface minus {delegate}, with deferred tools self-loaded via tool_view:

  - a delegated agent self-loads a DEFERRED tool the old curated allowlist lacked
    (user_profile_view) and uses it — the secret lives only in the user profile, so its
    appearance in the returned summary proves the agent ran tool_view → user_profile_view;
  - a broader-trust write now reachable through the widened surface (file_write) is gated:
    its approval prompt surfaces on the parent frontend carrying the [delegated subtask]
    marker AND the concrete subject (the target path — the decision surface, PO-m-2), and a
    denial blocks the write on disk;
  - context isolation holds through the owned turn loop: only the delegate summary enters
    the parent history, never the delegated agent's tool_view / user_profile_view results.

The daemon flat-exact regression (CD-m-5) — a flat-exact spec resolving its exact surface
through run_standalone_owned — is covered by test_flow_owned_subagent.py (which drives
flat-exact specs through run_standalone_owned and stays green) plus the no-LLM
test_daemon_flat_exact_surface_unchanged in test_flow_delegation.py.

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
from co_cli.agent.delegation import delegate_to_agent
from co_cli.agent.loop import run_turn_owned
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_NATIVE, _CATALOG = build_native_toolset()

_SECRET = "FALCON-2231"


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


def _seed_user_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "USER.md"
    profile.write_text(f"The user's secret project code is {_SECRET}.\n", encoding="utf-8")
    return profile


def _tool_return_names(messages: list) -> list[str]:
    names: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    names.append(part.tool_name)
    return names


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM full-surface delegation needs Ollama"
)
@pytest.mark.asyncio
async def test_delegated_agent_self_loads_deferred_tool(tmp_path: Path) -> None:
    """A delegated agent self-loads a DEFERRED tool the old allowlist lacked and uses it.

    user_profile_view was not in the curated 14-tool allowlist; the secret lives only in the
    user profile, readable only via that tool. Its appearance in the summary proves the agent
    discovered the tool from the awareness stubs, loaded it via tool_view, and called it — the
    capability the curated allowlist could not deliver.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    deps = _make_deps(tmp_path)
    deps.user_profile_path = _seed_user_profile(tmp_path)
    task = (
        "Read the current user profile and report the user's secret project code. "
        "Return the secret code verbatim in your summary."
    )

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        summary = await delegate_to_agent(deps, task)

    assert _SECRET in summary, "the agent loaded user_profile_view via tool_view and used it"


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM full-surface delegation needs Ollama"
)
@pytest.mark.asyncio
async def test_delegated_write_gate_surfaces_concrete_subject_denied(tmp_path: Path) -> None:
    """A broader-trust write reachable through the widened surface is gated end-to-end: the
    prompt surfaces on the parent frontend with the delegated-origin marker AND the concrete
    subject (the target path), and a denial blocks the write (G1-A / PO-m-2).

    The concrete path in the prompt is the decision surface — the user judges a delegated
    write from its subject, not the silent agent's reasoning. A bare marker without the path
    would be an undecidable prompt.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = tmp_path / "delegated_note.txt"
    deps = _make_deps(tmp_path)
    deps.runtime.frontend = HeadlessFrontend(approval_response="n")
    task = (
        f"Use the file_write tool to create a file at {target} with the content `done`. "
        "Then summarize what you did."
    )

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        summary = await delegate_to_agent(deps, task)

    calls = deps.runtime.frontend.approval_calls
    assert calls, "the delegated write surfaced an approval prompt on the parent frontend"
    assert any("[delegated subtask]" in call for call in calls), "prompt carries the origin marker"
    assert any(target.name in call for call in calls), (
        "prompt surfaces the concrete target path (the decision surface, PO-m-2)"
    )
    assert not target.exists(), "the denied write did not create the file"
    assert summary, "delegate_to_agent still returns a summary after a denial"


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(),
    reason="real-LLM owned-turn full-surface delegation needs Ollama",
)
@pytest.mark.asyncio
async def test_owned_turn_delegated_deferred_tool_isolated(tmp_path: Path) -> None:
    """Through the owned turn loop, the orchestrator delegates a subtask needing a DEFERRED
    tool; only the delegate summary enters parent history — not the agent's tool_view or
    user_profile_view results (context isolation across the widened surface).
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    deps = _make_deps(tmp_path)
    deps.user_profile_path = _seed_user_profile(tmp_path)
    frontend = HeadlessFrontend(approval_response="y")
    user_input = (
        "Use the delegate tool to read the user profile and find the user's secret project "
        "code. Then tell me the secret code."
    )

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 3):
        turn = await run_turn_owned(
            user_input=user_input,
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert turn.outcome == "continue"
    return_names = _tool_return_names(turn.messages)
    assert "delegate" in return_names, "the delegate summary entered parent history"
    assert "user_profile_view" not in return_names, (
        "the agent's deferred-tool result stayed isolated"
    )
    assert "tool_view" not in return_names, "the agent's tool_view result stayed isolated"

    delegate_returns = [
        part
        for msg in turn.messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "delegate"
    ]
    assert len(delegate_returns) == 1
    assert _SECRET in str(delegate_returns[0].content)
