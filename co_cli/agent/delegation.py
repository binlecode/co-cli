"""In-turn agent-as-tool delegation — full delegated agent (Phase 2.5 + 3.5 + 3.6).

The ``delegate`` tool (``co_cli/tools/system/delegate.py``) calls ``delegate_to_agent``,
which runs a delegated agent in an isolated forked context and returns only a distilled
one-field summary. The delegated agent is a full agent, not a lesser one: it inherits the
orchestrator's own visibility surface (native + MCP, DEFERRED tools self-loaded via
``tool_view``) minus a one-tool structural blocklist (``_DELEGATE_AGENT_BLOCKLIST``), and
decides for itself which tools the subtask needs (Phase 3.6 — ``SurfaceModeEnum.VISIBILITY_MODEL``).
Its intermediate tool calls/results never enter the parent's history — the
context-isolation contract. Approval-required calls surface on the parent's terminal
via the inline-approval collector (Phase 3.5); the delegated agent never bypasses the gate,
so durable writes are gated rather than withheld.

Recursion is bounded: the delegated surface excludes ``delegate`` and ``agent_depth`` is the
backstop (``DELEGATE_DEPTH_CAP``). The delegated agent draws tool-dispatch concurrency from
its own semaphore (``fork_deps(..., share_dispatch_sem=False)``), so it never starves behind
the parent slot held for the synchronous ``delegate`` call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from co_cli.agent.loop import run_standalone_owned
from co_cli.agent.spec import SurfaceModeEnum, TaskAgentSpec
from co_cli.config.skills import REVIEW_MAX_ITERATIONS
from co_cli.deps import fork_deps
from co_cli.tools.deferred_prompt import build_deferred_tool_awareness_prompt

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


DELEGATE_DEPTH_CAP = 1
"""Maximum delegation depth. The delegated agent runs at ``agent_depth == 1`` and cannot
delegate (the tool is absent from its surface); ``agent_depth`` is the backstop. Cutover
value, not a design ceiling — a future nested-delegation need re-opens this as scope."""

DELEGATE_AGENT_BUDGET = REVIEW_MAX_ITERATIONS
"""Delegated-agent model-request budget, on the daemon-review scale
(``co_cli/daemons/dream/_reviewer.py`` ``default_budget=REVIEW_MAX_ITERATIONS``)."""

_NO_RESULT_FALLBACK = "Delegated subtask did not produce a result within its budget."

_DELEGATE_AGENT_BLOCKLIST = frozenset({"delegate"})
"""The only tool withheld from the delegated agent's surface — the recursion / depth-cap
invariant (belt-and-suspenders with the ``agent_depth`` guard). Nothing else is withheld:
durable-write safety is recovered at the approval gate (Phase 3.5 propagation to the parent),
which co has and the inherit-minus-blocklist peers lack."""


class DelegationResult(BaseModel):
    """Structured output of a delegated agent — a single distilled summary."""

    summary: str


def _delegate_agent_instructions(deps: CoDeps) -> str:
    """Per-step delegated-agent instructions: the role brief plus the live deferred-tool stubs.

    Recomputed each step by the owned loop (CD-M-1), so once the delegated agent loads a
    deferred tool via tool_view it stops being advertised as loadable (the stub builder skips
    already-revealed tools). The blocklist's only member, ``delegate``, is ALWAYS-visibility
    and the stub builder emits DEFERRED-only tools, so it can never appear here — the full
    tool_catalog is passed through unfiltered.
    """
    base = (
        "You are a focused agent handling one delegated subtask for the main agent. "
        "You have the same full tool surface as the main agent: read and search, and act — "
        "run commands, write and patch files, and the rest. Some tools are not loaded up "
        "front; to use one, pass its exact name to tool_view to load it, then call it. "
        "Sensitive actions are gated by user approval; if an action is denied, adapt and "
        "continue with what you can do. When done, call the final_result tool with a single "
        "concise `summary` that distills the outcome into what the main agent needs to "
        "continue. You have no user channel — do not ask questions. Keep the summary "
        "self-contained: the main agent sees only your summary, never your intermediate "
        "tool calls or their results."
    )
    stubs = build_deferred_tool_awareness_prompt(deps.tool_catalog, deps.runtime.revealed_tools)
    if stubs:
        return f"{base}\n\n{stubs}"
    return base


DELEGATE_AGENT_SPEC = TaskAgentSpec(
    name="delegate_agent",
    instructions=_delegate_agent_instructions,
    tool_names=(),
    output_type=DelegationResult,
    default_budget=DELEGATE_AGENT_BUDGET,
    surface_mode=SurfaceModeEnum.VISIBILITY_MODEL,
)


async def delegate_to_agent(parent_deps: CoDeps, task: str) -> str:
    """Run a write-capable delegated agent on ``task`` in an isolated forked context.

    Depth-guarded: refuses at ``DELEGATE_DEPTH_CAP`` without forking. The delegated agent gets
    its own tool-dispatch semaphore (``share_dispatch_sem=False``) so it never contends with
    the parent slot held for the synchronous delegate call. Returns the delegated agent's
    distilled summary, or a fixed fallback string when it exhausts its budget / hard-stops
    without a ``final_result`` call (``run_standalone_owned`` returns ``None``). Its usage
    rolls into the parent turn via the shared ``usage_accumulator`` (fork shares it by
    reference) — no extra accounting here. ``CancelledError`` propagates, cancelling the run.

    Approval propagation (Phase 3.5): the delegated surface includes every approval-required
    tool the orchestrator has (Phase 3.6), so the driver runs with ``propagate_approvals=True``
    and the parent's ``runtime.frontend`` — an approval-required call surfaces on the
    parent's terminal. ``fork_deps`` reset the forked runtime, so the frontend is threaded
    explicitly, never inherited; a headless parent (``frontend is None``) makes the delegated
    agent auto-deny, so a write-capable agent never acts unprompted.
    """
    if parent_deps.runtime.agent_depth >= DELEGATE_DEPTH_CAP:
        return (
            f"Delegation refused: already at maximum delegation depth ({DELEGATE_DEPTH_CAP}). "
            "Do this subtask inline instead of delegating."
        )

    agent_deps = fork_deps(parent_deps, share_dispatch_sem=False)
    frontend = parent_deps.runtime.frontend
    result = await run_standalone_owned(
        DELEGATE_AGENT_SPEC,
        agent_deps,
        task,
        settings=parent_deps.model.settings,
        propagate_approvals=True,
        frontend=frontend,
    )
    if result is None:
        return _NO_RESULT_FALLBACK
    return result.summary
