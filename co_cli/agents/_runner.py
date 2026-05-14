"""Standalone agent runner for background agents (session_reviewer, skill_curator)."""

from __future__ import annotations

from copy import copy
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from pydantic_ai.usage import RunUsage, UsageLimits

if TYPE_CHECKING:
    from co_cli.deps import CoDeps

_TRACER = trace.get_tracer("co-cli.agents")


async def _run_agent_standalone(
    agent: Any,
    prompt: str,
    deps: CoDeps,
    budget: int,
    model_settings: Any,
    *,
    role: str,
) -> tuple[Any, RunUsage, str]:
    """Run a background agent without usage merge or ModelRetry.

    Used by session_reviewer and skill_curator — differs from _run_agent_in_turn:
    - Takes already-forked deps (no RunContext).
    - Does NOT merge usage into turn_usage.
    - Does NOT raise ModelRetry on failure (plain exceptions propagate).
    """
    with _TRACER.start_as_current_span(role) as span:
        span.set_attribute("agent.role", role)
        span.set_attribute("agent.request_limit", budget)
        result = await agent.run(
            prompt,
            deps=deps,
            usage_limits=UsageLimits(request_limit=budget),
            model_settings=model_settings,
            metadata={"session_id": deps.session.session_path.stem[-8:], "role": role},
        )
        usage = result.usage()
        span.set_attribute("agent.requests_used", usage.requests)
        return result.output, copy(usage), result.run_id
