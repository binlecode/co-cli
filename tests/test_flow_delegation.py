"""In-turn agent-as-tool delegation (Phase 2.5).

The behavioral net for the read-mostly child:
  - depth-cap refusal returns before any child runs (recursion bound);
  - share_dispatch_sem=False decouples the child's tool-dispatch semaphore from the
    parent's (the CD-M-1 no-starvation contract);
  - the child spec resolves to a real read-mostly tool surface that excludes delegate;
  - a delegated child genuinely reads via its tools and distills (a secret only readable
    from a file appears in the returned summary) with its tokens rolled into the parent;
  - run_standalone_owned returns None on budget exhaustion (the source the delegate
    None-fallback guards) — delegate_to_child maps that to a fixed string, not AttributeError;
  - through the owned loop, the child's intermediate tool results never enter the parent
    history — only the delegate summary does (the context-isolation contract).

Real-LLM tests skip unless Ollama is configured; the model is warmed outside the timeout.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
from co_cli.agent.delegation import (
    DELEGATE_CHILD_SPEC,
    DELEGATE_DEPTH_CAP,
    delegate_to_child,
)
from co_cli.agent.loop import _build_subagent_toolset, run_standalone_owned, run_turn_owned
from co_cli.agent.spec import TaskAgentSpec
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState, fork_deps
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.agent_tool import AGENT_TOOL_ATTR, TOOL_REGISTRY_BY_NAME
from co_cli.tools.shell_backend import ShellBackend

_NATIVE, _CATALOG = build_native_toolset()


def _make_parent_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=build_model(_CONFIG_NO_MCP.llm),
        toolset=assemble_routing_toolset(_NATIVE, []),
        tool_catalog=_CATALOG,
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
    )


def _tool_return_names(messages: list) -> list[str]:
    names: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    names.append(part.tool_name)
    return names


# ---------------------------------------------------------------------------
# Deterministic, no-LLM contracts.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_refused_at_depth_cap_without_running_child() -> None:
    """At max depth, delegate_to_child refuses and never forks/runs a child.

    Failure mode: a child re-delegates (or runs at all) at the depth cap, blowing the
    recursion bound. Observed via the refusal string and zero token spend (no child ran).
    """
    deps = _make_parent_deps()
    deps.runtime = CoRuntimeState(agent_depth=DELEGATE_DEPTH_CAP)

    result = await delegate_to_child(deps, "Read something and summarize it.")

    assert "refused" in result.lower()
    assert deps.usage_accumulator.input_tokens == 0
    assert deps.usage_accumulator.output_tokens == 0


def test_fork_with_own_dispatch_sem_decouples_child_from_parent() -> None:
    """share_dispatch_sem=False gives the child its own semaphore; default shares the parent's.

    Failure mode: the in-turn child draws from the parent-held pool and starves/deadlocks
    behind the slot the synchronous delegate call holds (CD-M-1).
    """
    parent = _make_parent_deps()

    own_sem_child = fork_deps(parent, share_dispatch_sem=False)
    assert own_sem_child.tool_dispatch_sem is not parent.tool_dispatch_sem

    shared_child = fork_deps(parent)
    assert shared_child.tool_dispatch_sem is parent.tool_dispatch_sem


def test_child_spec_builds_read_mostly_surface_excluding_delegate() -> None:
    """The child spec resolves to a real, all-non-approval tool surface that omits delegate.

    Failure mode: an unknown tool name (would raise at child build time), an approval-gated
    tool on a child with no approval channel, or delegate present (re-delegation path).
    Drives the production resolver _build_subagent_toolset (raises ValueError on a bad name).
    """
    _build_subagent_toolset(DELEGATE_CHILD_SPEC)

    assert "delegate" not in DELEGATE_CHILD_SPEC.tool_names
    assert DELEGATE_CHILD_SPEC.tool_names
    for name in DELEGATE_CHILD_SPEC.tool_names:
        fn = TOOL_REGISTRY_BY_NAME[name]
        info = getattr(fn, AGENT_TOOL_ATTR)
        assert info.is_approval_required is False, f"{name} requires approval"


# ---------------------------------------------------------------------------
# Real-Ollama: the child gathers, distills, and isolates.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM delegation needs Ollama"
)
@pytest.mark.asyncio
async def test_delegate_to_child_reads_file_and_rolls_usage_into_parent(tmp_path) -> None:
    """A delegated child genuinely executes a read tool and distills; tokens roll into parent.

    The secret lives only inside a file, so its appearance in the returned summary proves
    the child ran file_read and distilled it. Parent usage rising proves child tokens roll
    into the parent turn via the shared accumulator.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    fact_file = tmp_path / "fact.txt"
    fact_file.write_text("The project launch code is ZEBRA-7793.\n", encoding="utf-8")

    deps = _make_parent_deps()
    deps.file_search_roots = [tmp_path]
    task = (
        f"Read the file at {fact_file} and report the launch code it contains. "
        "Return the launch code in your summary."
    )

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        summary = await delegate_to_child(deps, task)

    assert "ZEBRA-7793" in summary
    assert deps.usage_accumulator.input_tokens > 0
    assert deps.usage_accumulator.output_tokens > 0


@pytest.mark.skipif(not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM subagent needs Ollama")
@pytest.mark.asyncio
async def test_run_standalone_owned_returns_none_when_budget_exhausted(tmp_path) -> None:
    """run_standalone_owned returns None when the child spends its budget without final_result.

    This is the source the delegate None-fallback guards: a budget-1 child instructed to
    call a non-final tool first exits the loop with no validated result. delegate_to_child
    maps this None to a fixed string instead of dereferencing result.summary (AttributeError).
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    fact_file = tmp_path / "fact.txt"
    fact_file.write_text("anything", encoding="utf-8")

    class _Note(BaseModel):
        summary: str

    spec = TaskAgentSpec(
        name="budget_exhaust_probe",
        instructions=lambda deps: (
            f"Your first action must be to call the file_read tool on {fact_file}. "
            "Do not call final_result on your first step."
        ),
        tool_names=("file_read",),
        output_type=_Note,
        default_budget=1,
    )
    deps = _make_parent_deps()
    deps.file_search_roots = [tmp_path]

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        result = await run_standalone_owned(spec, deps, "Read the file.")

    assert result is None


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM delegation needs Ollama"
)
@pytest.mark.asyncio
async def test_delegate_to_child_cancellation_propagates(tmp_path) -> None:
    """Cancelling the awaiting parent cancels the child — CancelledError propagates cleanly.

    Failure mode: delegate_to_child swallows cancellation and leaves an orphaned child run.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    fact_file = tmp_path / "fact.txt"
    fact_file.write_text("The launch code is ZEBRA-7793.\n", encoding="utf-8")

    deps = _make_parent_deps()
    deps.file_search_roots = [tmp_path]
    task = f"Read the file at {fact_file} and summarize its contents."
    child_task = asyncio.create_task(delegate_to_child(deps, task))

    await asyncio.sleep(1.0)
    child_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await child_task


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM owned-turn delegation needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_turn_delegate_isolates_child_transcript(tmp_path) -> None:
    """Through the owned loop, only the delegate summary enters parent history — not the
    child's intermediate tool results (the context-isolation contract).

    The child must read a file to learn the secret; the secret surfaces in the parent's
    single delegate ToolReturnPart, while the child's file_read ToolReturnPart is absent
    from parent history.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    fact_file = tmp_path / "fact.txt"
    fact_file.write_text("The project launch code is ZEBRA-7793.\n", encoding="utf-8")

    deps = _make_parent_deps()
    deps.file_search_roots = [tmp_path]
    frontend = HeadlessFrontend(approval_response="y")
    user_input = (
        f"Use the delegate tool to read the file at {fact_file} and find the launch code. "
        "Then tell me the launch code."
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
    assert "delegate" in return_names
    assert "file_read" not in return_names

    delegate_returns = [
        part
        for msg in turn.messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "delegate"
    ]
    assert len(delegate_returns) == 1
    assert "ZEBRA-7793" in str(delegate_returns[0].content)
