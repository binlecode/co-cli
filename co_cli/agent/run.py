"""Task-agent runner — standalone daemon execution.

run_standalone: takes already-forked deps, opens own span, never depth-checks,
does not merge usage, lets exceptions propagate plain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.usage import UsageLimits

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
    from co_cli.agent.build import build_task_agent
    from co_cli.observability.tracing import pop_span, push_span
    from co_cli.observability.usage import record_usage

    if deps.model is None:
        raise ValueError(f"{spec.name}: run_standalone requires deps.model to be set.")

    if deps.config.llm.use_owned_loop:
        from co_cli.agent.loop import run_standalone_owned

        await run_standalone_owned(spec, deps, prompt)
        return
    request_limit = spec.default_budget
    settings = deps.model.settings_noreason
    agent = build_task_agent(spec, deps, deps.model.model)

    agent_name = getattr(agent, "name", None) or "<unknown>"
    push_span(
        f"invoke_agent {agent_name}",
        kind="agent",
        attributes={
            "co.agent.role": spec.name,
            "co.agent.model": getattr(deps.model.model, "model_name", str(deps.model.model)),
            "co.agent.request_limit": request_limit,
        },
    )
    try:
        result = await agent.run(
            prompt,
            deps=deps,
            usage_limits=UsageLimits(request_limit=request_limit),
            model_settings=settings,
        )
    except BaseException as exc:
        pop_span(status="ERROR", status_msg=str(exc))
        raise
    usage = result.usage()
    # Record this run's final cumulative usage once at the run boundary, into the
    # caller-forked accumulator (daemon-origin) — path-agnostic, no RunContext needed.
    record_usage(deps, usage)
    pop_span(
        attributes={
            "co.agent.requests_used": getattr(usage, "requests", None),
            "co.agent.final_result": str(result.output),
        },
    )
