"""In-turn agent-as-tool delegation — write-capable child agent (Phase 2.5 + 3.5).

The ``delegate`` tool (``co_cli/tools/system/delegate.py``) calls ``delegate_to_child``,
which runs a child agent in an isolated forked context with a curated tool surface (read,
search, and — Phase 3.5 — shell + file write/patch) and returns only a distilled one-field
summary. The child's intermediate tool calls/results never enter the parent's history — the
context-isolation contract. Approval-required child calls surface on the parent's terminal
via the inline-approval collector (Phase 3.5); the child never bypasses the gate.

Recursion is bounded: the child surface excludes ``delegate`` and ``agent_depth`` is the
backstop (``DELEGATE_DEPTH_CAP``). The child draws tool-dispatch concurrency from its own
semaphore (``fork_deps(..., share_dispatch_sem=False)``), so it never starves behind the
parent slot held for the synchronous ``delegate`` call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from co_cli.agent.loop import run_standalone_owned
from co_cli.agent.spec import TaskAgentSpec
from co_cli.config.skills import REVIEW_MAX_ITERATIONS
from co_cli.deps import fork_deps

if TYPE_CHECKING:
    from co_cli.deps import CoDeps


DELEGATE_DEPTH_CAP = 1
"""Maximum delegation depth. The child runs at ``agent_depth == 1`` and cannot delegate
(the tool is absent from its surface); ``agent_depth`` is the backstop. Cutover value, not
a design ceiling — a future nested-delegation need re-opens this as scope."""

DELEGATE_CHILD_BUDGET = REVIEW_MAX_ITERATIONS
"""Child model-request budget, on the daemon-review scale
(``co_cli/daemons/dream/_reviewer.py`` ``default_budget=REVIEW_MAX_ITERATIONS``)."""

_NO_RESULT_FALLBACK = "Delegated subtask did not produce a result within its budget."


class DelegationResult(BaseModel):
    """Structured output of a delegated child — a single distilled summary."""

    summary: str


_CHILD_TOOL_NAMES = (
    "file_read",
    "file_search",
    "web_search",
    "web_fetch",
    "memory_search",
    "memory_view",
    "session_search",
    "session_view",
    "todo_read",
    "capabilities_check",
    "image_view",
    "shell_exec",
    "file_write",
    "file_patch",
)


def _delegate_child_instructions(deps: CoDeps) -> str:
    return (
        "You are a focused sub-agent handling one delegated subtask for the main agent. "
        "Use your tools to do what the task asks: you can read and search, and you can "
        "act — run commands, write and patch files. Sensitive actions are gated by user "
        "approval; if an action is denied, adapt and continue with what you can do. When "
        "done, call the final_result tool with a single concise `summary` that distills "
        "the outcome into what the main agent needs to continue. You have no user "
        "channel — do not ask questions. Keep the summary self-contained: the main agent "
        "sees only your summary, never your intermediate tool calls or their results."
    )


DELEGATE_CHILD_SPEC = TaskAgentSpec(
    name="delegate_child",
    instructions=_delegate_child_instructions,
    tool_names=_CHILD_TOOL_NAMES,
    output_type=DelegationResult,
    default_budget=DELEGATE_CHILD_BUDGET,
)


async def delegate_to_child(parent_deps: CoDeps, task: str) -> str:
    """Run a write-capable child agent on ``task`` in an isolated forked context.

    Depth-guarded: refuses at ``DELEGATE_DEPTH_CAP`` without forking. The child gets its
    own tool-dispatch semaphore (``share_dispatch_sem=False``) so it never contends with
    the parent slot held for the synchronous delegate call. Returns the child's distilled
    summary, or a fixed fallback string when the child exhausts its budget / hard-stops
    without a ``final_result`` call (``run_standalone_owned`` returns ``None``). Child usage
    rolls into the parent turn via the shared ``usage_accumulator`` (fork shares it by
    reference) — no extra accounting here. ``CancelledError`` propagates, cancelling the child.

    Approval propagation (Phase 3.5): the child surface includes write-capable tools
    (shell, file write/patch), so the driver runs with ``propagate_approvals=True`` and the
    parent's ``runtime.frontend`` — an approval-required child call surfaces on the parent's
    terminal. ``fork_deps`` reset the child runtime, so the frontend is threaded explicitly,
    never inherited; a headless parent (``frontend is None``) makes the child auto-deny, so a
    write-capable child never acts unprompted.
    """
    if parent_deps.runtime.agent_depth >= DELEGATE_DEPTH_CAP:
        return (
            f"Delegation refused: already at maximum delegation depth ({DELEGATE_DEPTH_CAP}). "
            "Do this subtask inline instead of delegating."
        )

    child_deps = fork_deps(parent_deps, share_dispatch_sem=False)
    frontend = parent_deps.runtime.frontend
    result = await run_standalone_owned(
        DELEGATE_CHILD_SPEC,
        child_deps,
        task,
        settings=parent_deps.model.settings,
        propagate_approvals=True,
        frontend=frontend,
    )
    if result is None:
        return _NO_RESULT_FALLBACK
    return result.summary
