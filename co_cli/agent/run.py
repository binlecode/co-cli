"""Task-agent runner — standalone daemon execution.

run_standalone: takes already-forked deps, opens own span, never depth-checks,
does not merge usage, lets exceptions propagate plain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.agent.spec import TaskAgentSpec
    from co_cli.deps import CoDeps


async def run_standalone(
    spec: TaskAgentSpec,
    deps: CoDeps,
    prompt: str,
) -> None:
    """Run a task agent as a daemon — caller-forked deps, own span, no merge, plain exceptions.

    No depth check (daemons are top-level). No usage merge (no parent turn).
    Exceptions propagate plain to the caller's daemon-specific error handling
    (timeout, report write-on-fail).

    Request limit is spec.default_budget; model settings are
    deps.model.settings_noreason — standalone runs are background daemon
    derivations (synthesis, review), which never reason at the model level
    (reasoning techniques live wholly in the prompt); thinking-off also lifts
    the output cap.

    Args:
        spec: The task agent spec.
        deps: Already-forked deps (caller did fork_deps_for_reviewer).
        prompt: User prompt.
    """
    from co_cli.agent.loop import run_standalone_owned

    if deps.model is None:
        raise ValueError(f"{spec.name}: run_standalone requires deps.model to be set.")

    await run_standalone_owned(spec, deps, prompt)
