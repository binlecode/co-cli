"""Owned-loop inline-approval flow tests (real Ollama).

The behavioral net for Phase 3: the owned loop must gate a destructive shell action behind
the user's approval, honor a scripted denial (the side effect does NOT happen — verified on
disk) while the turn continues, execute on a scripted approval, and skip the prompt entirely
when the subject is auto-approved by a pre-seeded session rule.

These cover the deny-blocks / approve-executes / auto-approve contract a unit test can't —
the full model→collector→dispatch→shell-body path. Real-LLM tests skip unless Ollama is
configured; the model is warmed outside the call timeout.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
from co_cli.agent.loop import run_turn_owned
from co_cli.deps import (
    ApprovalKindEnum,
    CoDeps,
    CoSessionState,
    SessionApprovalRule,
)
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_NATIVE, _CATALOG = build_native_toolset()

_DELETE_ASK = (
    "Use the shell_exec tool to run exactly `rm target.md` in the current directory to "
    "delete the file. Use only shell_exec — no other tool."
)


def _make_deps(workspace: Path, session: CoSessionState) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=build_model(_CONFIG_NO_MCP.llm),
        toolset=assemble_routing_toolset(_NATIVE, []),
        tool_catalog=_CATALOG,
        config=_CONFIG_NO_MCP,
        session=session,
        workspace_dir=workspace,
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
    )


def _seed_target(workspace: Path) -> Path:
    target = workspace / "target.md"
    target.write_text("# throwaway\nowned-approval flow test\n", encoding="utf-8")
    return target


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM owned approval flow needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_denied_destructive_action_not_executed(tmp_path: Path) -> None:
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = _seed_target(tmp_path)
    deps = _make_deps(tmp_path, CoSessionState())
    frontend = HeadlessFrontend(approval_response="n")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        turn = await run_turn_owned(
            user_input=_DELETE_ASK,
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert frontend.approval_calls, "the destructive shell action was gated for approval"
    assert target.exists(), "denied action did not delete the file"
    assert turn.outcome == "continue", "the turn continues after a denial (not a crash/halt)"


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM owned approval flow needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_approved_destructive_action_executes(tmp_path: Path) -> None:
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = _seed_target(tmp_path)
    deps = _make_deps(tmp_path, CoSessionState())
    frontend = HeadlessFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        await run_turn_owned(
            user_input=_DELETE_ASK,
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert frontend.approval_calls, "the destructive shell action was gated for approval"
    assert not target.exists(), "approved action executed and deleted the file"


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM owned approval flow needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_auto_approved_subject_skips_prompt(tmp_path: Path) -> None:
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = _seed_target(tmp_path)
    session = CoSessionState()
    # Pre-seed the shell "rm" utility as an approved subject this session.
    session.session_approval_rules.append(
        SessionApprovalRule(kind=ApprovalKindEnum.SHELL, value="rm")
    )
    deps = _make_deps(tmp_path, session)
    # Scripted to DENY: if the collector prompted, the action would be blocked. It must not.
    frontend = HeadlessFrontend(approval_response="n")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        await run_turn_owned(
            user_input=_DELETE_ASK,
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert not frontend.approval_calls, "auto-approved subject is not prompted"
    assert not target.exists(), "auto-approved action executed without a prompt"
