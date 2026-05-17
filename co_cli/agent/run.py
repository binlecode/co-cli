"""Task-agent runners — in-turn and standalone.

run_in_turn: always depth-checks, forks deps, opens own span, merges usage
into parent turn, raises ModelRetry(spec.error_message) on failure.

run_standalone: takes already-forked deps, opens own span, never depth-checks,
does not merge usage, lets exceptions propagate plain. Does not consult
spec.error_message — daemons propagate plain exceptions.

_run_attempt: low-level primitive shared by both runners. Used by
web_research's tool wrapper to drive two attempts inside a single outer
span (preserves single-span retry topology).
"""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolReturn
from pydantic_ai.usage import RunUsage, UsageLimits

from co_cli.deps import fork_deps
from co_cli.tools.tool_io import tool_output

if TYPE_CHECKING:
    from pydantic_ai import RunContext

    from co_cli.agent.spec import TaskAgentSpec
    from co_cli.deps import CoDeps

MAX_AGENT_DEPTH: int = 2


def _merge_turn_usage(ctx: RunContext[CoDeps], usage: RunUsage) -> None:
    """Merge delegation agent usage into the parent turn's authoritative usage accumulator."""
    if ctx.deps.runtime.turn_usage is None:
        ctx.deps.runtime.turn_usage = usage
    else:
        ctx.deps.runtime.turn_usage.incr(usage)


async def _run_attempt(
    spec: TaskAgentSpec,
    ctx: RunContext[CoDeps],
    prompt: str,
    budget: int,
    child_deps: CoDeps,
) -> tuple[Any, RunUsage, str]:
    """Run one task-agent attempt — builds the agent, runs once, raises ModelRetry on failure.

    Used by run_in_turn and by web_research's tool wrapper (which manages its
    own outer span to cover both attempts of the retry-on-empty loop).
    Caller owns fork_deps, span, depth check, and usage merge.
    """
    from co_cli.agent.build import build_task_agent

    # ctx.deps.model None-check is enforced by the caller (run_in_turn / web_research wrapper).
    model_obj = ctx.deps.model.model  # type: ignore[union-attr]
    model_settings = ctx.deps.model.settings  # type: ignore[union-attr]
    agent = build_task_agent(spec, child_deps, model_obj)
    try:
        result = await agent.run(
            prompt,
            deps=child_deps,
            usage_limits=UsageLimits(request_limit=budget),
            model_settings=model_settings,
            metadata={
                "session_id": ctx.deps.session.session_path.stem[-8:],
                "role": spec.name,
                "request_limit": budget,
            },
        )
    except Exception as exc:
        raise ModelRetry(spec.error_message) from exc
    return result.output, copy(result.usage()), result.run_id


async def run_in_turn(
    spec: TaskAgentSpec,
    ctx: RunContext[CoDeps],
    prompt: str,
    budget: int | None = None,
) -> ToolReturn:
    """Run a task agent inside a parent turn — depth-check, fork, span, usage-merge.

    Always performs the depth check. Forks deps via fork_deps(ctx.deps),
    opens an OTel span named spec.name, builds and runs the task agent,
    merges usage into ctx.deps.runtime.turn_usage, raises
    ModelRetry(spec.error_message) on failure, and formats the ToolReturn
    with spec.name as role tag.
    """
    if ctx.deps.runtime.agent_depth >= MAX_AGENT_DEPTH:
        raise ModelRetry(
            f"Delegation depth limit reached ({MAX_AGENT_DEPTH}). Handle this task directly."
        )
    if not ctx.deps.model:
        raise ModelRetry(f"{spec.name} agent is unavailable — handle this task directly.")

    request_limit = budget if budget else spec.default_budget
    model_obj = ctx.deps.model.model

    child_deps = fork_deps(ctx.deps)
    child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback

    output, usage, run_id = await _run_attempt(spec, ctx, prompt, request_limit, child_deps)
    _merge_turn_usage(ctx, usage)
    requests_used = usage.requests

    display = f"{output.result}\n[{spec.name} · {model_obj} · {requests_used}/{request_limit} req]"
    return tool_output(
        display,
        ctx=ctx,
        role=spec.name,
        model_name=str(model_obj),
        requests_used=requests_used,
        request_limit=request_limit,
        run_id=run_id,
    )


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
        deps: Already-forked deps (caller did fork_deps_for_reviewer / fork_deps_for_curator).
        prompt: User prompt.
        budget: Request limit override (defaults to spec.default_budget).
        model_settings: Optional override; defaults to deps.model.settings.
    """
    from co_cli.agent.build import build_task_agent

    if deps.model is None:
        raise ValueError(f"{spec.name}: run_standalone requires deps.model to be set.")
    request_limit = budget if budget else spec.default_budget
    settings = model_settings if model_settings is not None else deps.model.settings
    agent = build_task_agent(spec, deps, deps.model.model)

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
    usage = result.usage()
    return result.output, copy(usage), result.run_id
