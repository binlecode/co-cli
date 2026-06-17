"""Task-agent runner — standalone daemon execution.

run_standalone: takes already-forked deps, opens own span, never depth-checks,
does not merge usage, lets exceptions propagate plain. Does not consult
spec.error_message — daemons propagate plain exceptions.
"""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING, Any

from pydantic_ai.usage import RunUsage, UsageLimits

if TYPE_CHECKING:
    from co_cli.agent.spec import TaskAgentSpec
    from co_cli.deps import CoDeps


async def run_standalone(
    spec: TaskAgentSpec,
    deps: CoDeps,
    prompt: str,
    budget: int | None = None,
    model_settings: Any = None,
) -> tuple[Any, RunUsage, str]:
    """Run a task agent as a daemon — caller-forked deps, own span, no merge, plain exceptions.

    No depth check (daemons are top-level). No usage merge (no parent turn).
    Does not consult spec.error_message — exceptions propagate plain to the
    caller's daemon-specific error handling (timeout, report write-on-fail).

    Args:
        spec: The task agent spec.
        deps: Already-forked deps (caller did fork_deps_for_reviewer).
        prompt: User prompt.
        budget: Request limit override (defaults to spec.default_budget).
        model_settings: Optional override; defaults to deps.model.settings.
    """
    from co_cli.agent.build import build_task_agent
    from co_cli.observability.tracing import pop_span, push_span
    from co_cli.observability.usage import record_usage

    if deps.model is None:
        raise ValueError(f"{spec.name}: run_standalone requires deps.model to be set.")
    request_limit = budget if budget else spec.default_budget
    settings = model_settings if model_settings is not None else deps.model.settings
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
            metadata={
                "session_id": deps.session.session_path.stem[-8:],
                "role": spec.name,
                "request_limit": request_limit,
            },
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
    return result.output, copy(usage), result.run_id
