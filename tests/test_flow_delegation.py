"""In-turn agent-as-tool delegation (Phase 2.5 + 3.5 + 3.6).

The behavioral net for the write-capable delegated agent:
  - depth-cap refusal returns before any agent runs (recursion bound);
  - share_dispatch_sem=False decouples the delegated agent's tool-dispatch semaphore from
    the parent's (the CD-M-1 no-starvation contract);
  - the delegate spec resolves to the orchestrator's visibility surface minus {delegate},
    with file_write/file_patch registered sequential (the CD-M-1 concurrency contract), a
    DEFERRED tool hidden until tool_view-revealed (native and MCP), and the deferred-tool
    awareness stubs injected into the instructions (Phase 3.6);
  - a delegated agent genuinely reads via its tools and distills (a secret only readable
    from a file appears in the returned summary) with its tokens rolled into the parent;
  - delegate_to_agent threads the parent's frontend so a gated write surfaces on the parent
    terminal (Phase 3.5 approval propagation);
  - run_standalone_owned returns None on budget exhaustion (the source the delegate
    None-fallback guards) — delegate_to_agent maps that to a fixed string, not AttributeError;
  - through the owned loop, the delegated agent's intermediate tool results never enter the
    parent history — only the delegate summary does (the context-isolation contract).

Real-LLM tests skip unless Ollama is configured; the model is warmed outside the timeout.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.toolsets import FunctionToolset
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.core import assemble_routing_toolset, build_native_toolset
from co_cli.agent.delegation import (
    DELEGATE_AGENT_SPEC,
    DELEGATE_DEPTH_CAP,
    DelegationResult,
    _delegate_agent_instructions,
    delegate_to_agent,
)
from co_cli.agent.dispatch import get_visible_tools, make_run_context
from co_cli.agent.loop import _build_subagent_toolset, run_standalone_owned, run_turn_owned
from co_cli.agent.spec import SurfaceModeEnum, TaskAgentSpec
from co_cli.deps import (
    CoDeps,
    CoRuntimeState,
    CoSessionState,
    ToolInfo,
    ToolSourceEnum,
    VisibilityPolicyEnum,
    fork_deps,
)
from co_cli.display.headless import HeadlessFrontend
from co_cli.llm.factory import build_model
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
async def test_delegate_refused_at_depth_cap_without_running_agent() -> None:
    """At max depth, delegate_to_agent refuses and never forks/runs a delegated agent.

    Failure mode: a delegated agent re-delegates (or runs at all) at the depth cap, blowing
    the recursion bound. Observed via the refusal string and zero token spend (nothing ran).
    """
    deps = _make_parent_deps()
    deps.runtime = CoRuntimeState(agent_depth=DELEGATE_DEPTH_CAP)

    result = await delegate_to_agent(deps, "Read something and summarize it.")

    assert "refused" in result.lower()
    assert deps.usage_accumulator.input_tokens == 0
    assert deps.usage_accumulator.output_tokens == 0


def test_fork_with_own_dispatch_sem_decouples_agent_from_parent() -> None:
    """share_dispatch_sem=False gives the delegated agent its own semaphore; default shares
    the parent's.

    Failure mode: the in-turn agent draws from the parent-held pool and starves/deadlocks
    behind the slot the synchronous delegate call holds (CD-M-1).
    """
    parent = _make_parent_deps()

    own_sem_agent = fork_deps(parent, share_dispatch_sem=False)
    assert own_sem_agent.tool_dispatch_sem is not parent.tool_dispatch_sem

    shared_agent = fork_deps(parent)
    assert shared_agent.tool_dispatch_sem is parent.tool_dispatch_sem


async def _visible_names(deps: CoDeps) -> set[str]:
    """Resolve the per-turn visible tool names through the wired path (CD-m-3).

    Reads the actual deps.toolset via get_visible_tools — the same call build_tool_defs makes
    each step — so the test validates the wired surface, not a parallel construction.
    """
    ctx = make_run_context(deps)
    tools = await get_visible_tools(deps, ctx)
    return set(tools.keys())


async def _resolved_tools(deps: CoDeps) -> dict:
    ctx = make_run_context(deps)
    return await get_visible_tools(deps, ctx)


def _agent_visibility_deps() -> CoDeps:
    """Fork delegated-agent deps and wire the DELEGATE_AGENT_SPEC visibility surface, as
    run_standalone_owned does (deps.toolset = _build_subagent_toolset(spec, deps))."""
    parent = _make_parent_deps()
    agent_deps = fork_deps(parent, share_dispatch_sem=False)
    agent_deps.toolset = _build_subagent_toolset(DELEGATE_AGENT_SPEC, agent_deps)
    return agent_deps


@pytest.mark.asyncio
async def test_delegate_agent_visibility_surface_sees_orchestrator_minus_blocklist() -> None:
    """The delegated agent resolves the orchestrator's visibility surface minus {delegate}:
    ALWAYS tools visible, delegate absent, a DEFERRED tool hidden until tool_view-revealed,
    and non-concurrent-safe writes registered sequential (CD-M-1).

    Failure modes: delegate present (the re-delegation path); an ALWAYS tool missing (the
    agent can't act); a DEFERRED tool visible without a reveal (the deferral broke), or still
    hidden after a reveal (self-loading broke); file_write/file_patch in the concurrent batch.
    Asserts through the actual deps.toolset (CD-m-3), not a parallel filtered combine.
    """
    agent_deps = _agent_visibility_deps()

    visible = await _visible_names(agent_deps)
    assert "tool_view" in visible, "the agent must be able to load deferred tools"
    assert "file_write" in visible, "an ALWAYS tool is visible by default"
    assert "delegate" not in visible, "the recursion blocklist excludes delegate"
    assert "session_view" not in visible, "a DEFERRED tool is hidden until revealed"

    tools = await _resolved_tools(agent_deps)
    for name in ("file_write", "file_patch"):
        assert tools[name].tool_def.sequential is True, f"{name} must be sequential (CD-M-1)"

    agent_deps.runtime.revealed_tools.add("session_view")
    revealed = await _visible_names(agent_deps)
    assert "session_view" in revealed, "a tool_view reveal makes the DEFERRED tool visible"


@pytest.mark.asyncio
async def test_delegate_agent_visibility_surface_includes_mcp() -> None:
    """G1-B: a DEFERRED MCP tool on deps.mcp_toolsets is composed into the delegated surface
    and resolves hidden-until-revealed through the same path — MCP is not dropped.
    """
    parent = _make_parent_deps()

    async def mcp_stub_tool(ctx: RunContext[CoDeps]) -> str:
        return "ok"

    stub_toolset: FunctionToolset[CoDeps] = FunctionToolset()
    stub_toolset.add_function(mcp_stub_tool, name="mcp_stub_tool")
    parent.mcp_toolsets = [stub_toolset]
    parent.tool_catalog = {
        **parent.tool_catalog,
        "mcp_stub_tool": ToolInfo(
            name="mcp_stub_tool",
            description="A stub MCP tool.",
            is_approval_required=False,
            source=ToolSourceEnum.MCP,
            visibility=VisibilityPolicyEnum.DEFERRED,
            is_concurrent_safe=True,
            integration="stub_server",
        ),
    }

    agent_deps = fork_deps(parent, share_dispatch_sem=False)
    agent_deps.toolset = _build_subagent_toolset(DELEGATE_AGENT_SPEC, agent_deps)

    assert "mcp_stub_tool" not in await _visible_names(agent_deps), "MCP DEFERRED tool hidden"
    agent_deps.runtime.revealed_tools.add("mcp_stub_tool")
    assert "mcp_stub_tool" in await _visible_names(agent_deps), "revealed MCP tool visible"


@pytest.mark.asyncio
async def test_daemon_flat_exact_surface_unchanged() -> None:
    """A flat-exact spec resolves exactly its tool_names — the default mode is untouched by
    the visibility-mode branch (daemon specs stay byte-for-byte)."""
    spec = TaskAgentSpec(
        name="flat_probe",
        instructions=lambda deps: "probe",
        tool_names=("file_read", "memory_search"),
        output_type=DelegationResult,
        default_budget=2,
    )
    assert spec.surface_mode is SurfaceModeEnum.FLAT_EXACT

    deps = fork_deps(_make_parent_deps(), share_dispatch_sem=False)
    deps.toolset = _build_subagent_toolset(spec, deps)

    visible = await _visible_names(deps)
    assert visible == {"file_read", "memory_search"}, "flat-exact resolves exactly tool_names"


def test_delegate_agent_instructions_advertise_deferred_stubs() -> None:
    """The delegated-agent instructions carry the deferred-tool awareness stubs so the agent
    knows what it can self-load — without them it would never call tool_view (self-loading
    is dead).

    A revealed deferred tool stops being advertised (the stub builder skips it), which is why
    the instructions are recomputed per step (CD-M-1).
    """
    deps = _make_parent_deps()
    instructions = _delegate_agent_instructions(deps)

    assert "tool_view" in instructions, "the agent is told how to load deferred tools"
    assert "session_view" in instructions, "a DEFERRED tool is advertised as a loadable stub"

    deps.runtime.revealed_tools.add("session_view")
    after_reveal = _delegate_agent_instructions(deps)
    assert "session_view" not in after_reveal, "a revealed tool stops being advertised"


# ---------------------------------------------------------------------------
# Real-Ollama: the delegated agent gathers, distills, and isolates.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM delegation needs Ollama"
)
@pytest.mark.asyncio
async def test_delegate_to_agent_threads_parent_frontend_for_gated_write(tmp_path) -> None:
    """delegate_to_agent threads the parent's frontend (and propagate_approvals=True): a
    gated write surfaces on the parent terminal, and a denial blocks it.

    If propagate_approvals/frontend were not threaded, the collector would never run and the
    file_write would execute unprompted — so a recorded prompt plus an absent file is the
    joint proof. The headless (frontend-None) auto-deny path is covered by the run-turn gate
    in test_flow_delegation_approval.py.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    target = tmp_path / "made.txt"
    deps = _make_parent_deps()
    deps.workspace_dir = tmp_path
    deps.runtime.frontend = HeadlessFrontend(approval_response="n")
    task = (
        f"Use the file_write tool to create a file at {target} with the content `hi`. "
        "Then summarize what you did."
    )

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2):
        summary = await delegate_to_agent(deps, task)

    assert deps.runtime.frontend.approval_calls, "gated write surfaced on the parent frontend"
    assert not target.exists(), "the denied write did not create the file"
    assert summary, "delegate_to_agent still returns a summary"


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM delegation needs Ollama"
)
@pytest.mark.asyncio
async def test_delegate_to_agent_reads_file_and_rolls_usage_into_parent(tmp_path) -> None:
    """A delegated agent genuinely executes a read tool and distills; tokens roll into parent.

    The secret lives only inside a file, so its appearance in the returned summary proves the
    agent ran file_read and distilled it. Parent usage rising proves the agent's tokens roll
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
        summary = await delegate_to_agent(deps, task)

    assert "ZEBRA-7793" in summary
    assert deps.usage_accumulator.input_tokens > 0
    assert deps.usage_accumulator.output_tokens > 0


@pytest.mark.skipif(not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM subagent needs Ollama")
@pytest.mark.asyncio
async def test_run_standalone_owned_returns_none_when_budget_exhausted(tmp_path) -> None:
    """run_standalone_owned returns None when the agent spends its budget without final_result.

    This is the source the delegate None-fallback guards: a budget-1 agent instructed to call
    a non-final tool first exits the loop with no validated result. delegate_to_agent maps
    this None to a fixed string instead of dereferencing result.summary (AttributeError).
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
async def test_delegate_to_agent_cancellation_propagates(tmp_path) -> None:
    """Cancelling the awaiting parent cancels the delegated agent — CancelledError propagates
    cleanly.

    Failure mode: delegate_to_agent swallows cancellation and leaves an orphaned run.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    fact_file = tmp_path / "fact.txt"
    fact_file.write_text("The launch code is ZEBRA-7793.\n", encoding="utf-8")

    deps = _make_parent_deps()
    deps.file_search_roots = [tmp_path]
    task = f"Read the file at {fact_file} and summarize its contents."
    agent_task = asyncio.create_task(delegate_to_agent(deps, task))

    await asyncio.sleep(1.0)
    agent_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await agent_task


@pytest.mark.skipif(
    not _CONFIG_NO_MCP.llm.uses_ollama(), reason="real-LLM owned-turn delegation needs Ollama"
)
@pytest.mark.asyncio
async def test_owned_turn_delegate_isolates_delegated_transcript(tmp_path) -> None:
    """Through the owned loop, only the delegate summary enters parent history — not the
    delegated agent's intermediate tool results (the context-isolation contract).

    The delegated agent must read a file to learn the secret; the secret surfaces in the
    parent's single delegate ToolReturnPart, while the agent's file_read ToolReturnPart is
    absent from parent history.
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
