"""Tools for running focused tasks via purpose-built sub-agents."""

from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from typing import Any, NamedTuple

from opentelemetry import trace as otel_trace
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.usage import RunUsage, UsageLimits

from co_cli._model_factory import ResolvedModel
from co_cli.config import ROLE_ANALYSIS, ROLE_CODING, ROLE_REASONING, ROLE_RESEARCH
from co_cli.deps import CoDeps, make_subagent_deps
from co_cli.tools._subagent_builders import (
    make_analysis_agent,
    make_coder_agent,
    make_research_agent,
    make_thinking_agent,
)
from co_cli.tools.tool_output import tool_output

_TRACER = otel_trace.get_tracer("co-cli.subagent")


@dataclass(frozen=True)
class SubagentRoleConfig:
    """Declarative config for a single subagent role."""

    role: str
    factory: Callable[[ResolvedModel], Agent[CoDeps, Any]]
    max_requests_key: str
    error_msg: str
    guard_msg: str
    retry_on_empty: bool = False
    input_prepend: bool = False


SUBAGENT_ROLES: dict[str, SubagentRoleConfig] = {
    "coding": SubagentRoleConfig(
        role=ROLE_CODING, factory=make_coder_agent,
        max_requests_key="subagent_max_requests_coder",
        error_msg="Coding sub-agent failed — handle this task directly.",
        guard_msg="Coding sub-agent is unavailable — handle this task directly.",
    ),
    "research": SubagentRoleConfig(
        role=ROLE_RESEARCH, factory=make_research_agent,
        max_requests_key="subagent_max_requests_research",
        error_msg="Research sub-agent failed — handle this task directly.",
        guard_msg="Research sub-agent is unavailable — handle this task directly.",
        retry_on_empty=True,
    ),
    "analysis": SubagentRoleConfig(
        role=ROLE_ANALYSIS, factory=make_analysis_agent,
        max_requests_key="subagent_max_requests_analysis",
        error_msg="Analysis sub-agent failed — handle this task directly.",
        guard_msg="Analysis sub-agent is unavailable — handle this task directly.",
        input_prepend=True,
    ),
    "reasoning": SubagentRoleConfig(
        role=ROLE_REASONING, factory=make_thinking_agent,
        max_requests_key="subagent_max_requests_thinking",
        error_msg="Thinking sub-agent failed — handle this task directly.",
        guard_msg="Thinking sub-agent is unavailable — handle this task directly.",
    ),
}

_EXPECTED_ROLES = {"coding": ROLE_CODING, "research": ROLE_RESEARCH,
                   "analysis": ROLE_ANALYSIS, "reasoning": ROLE_REASONING}
for _key, _cfg in SUBAGENT_ROLES.items():
    assert _cfg.role == _EXPECTED_ROLES[_key], f"SUBAGENT_ROLES[{_key!r}].role mismatch"


def _format_output(
    role_key: str, data: Any, scope: str,
    role: str, model_name: str, requests_used: int, request_limit: int,
) -> tuple[str, dict[str, Any]]:
    """Format subagent output for display and extract metadata kwargs per role."""
    footer = f"[{role} · {model_name} · {requests_used}/{request_limit} req]"
    match role_key:
        case "coding":
            display = f"Scope: {scope}\nCoder analysis complete.\n{data.summary}\n{footer}"
            meta = dict(summary=data.summary, diff_preview=data.diff_preview,
                        files_touched=data.files_touched, confidence=data.confidence)
        case "research":
            src = "\n".join(f"- {s}" for s in data.sources) if data.sources else "No sources"
            display = f"Scope: {scope}\n{data.summary}\n\nSources:\n{src}\n{footer}"
            meta = dict(summary=data.summary, sources=data.sources, confidence=data.confidence)
        case "analysis":
            ev = "\n".join(f"- {e}" for e in data.evidence) if data.evidence else "No evidence"
            display = f"Scope: {scope}\n{data.conclusion}\n\nEvidence:\n{ev}\n{footer}"
            meta = dict(conclusion=data.conclusion, evidence=data.evidence, reasoning=data.reasoning)
        case "reasoning":
            steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(data.steps))
            display = f"Scope: {scope}\n{data.plan}\n\nSteps:\n{steps}\n\nConclusion:\n{data.conclusion}\n{footer}"
            meta = dict(plan=data.plan, steps=data.steps, conclusion=data.conclusion)
        case _:
            raise ValueError(f"Unknown role_key: {role_key!r}")
    return display, meta


class SubagentAttempt(NamedTuple):
    output: Any
    usage: RunUsage
    run_id: str


def _merge_turn_usage(ctx: RunContext[CoDeps], usage: RunUsage) -> None:
    """Merge sub-agent usage into the parent turn's authoritative usage accumulator."""
    if ctx.deps.runtime.turn_usage is None:
        ctx.deps.runtime.turn_usage = usage
    else:
        ctx.deps.runtime.turn_usage.incr(usage)


async def _run_subagent_attempt(
    agent: Any, prompt: str, ctx: RunContext[CoDeps],
    budget: int, model_settings: Any, error_msg: str,
) -> SubagentAttempt:
    """Run one subagent attempt with a fresh usage context.

    Creates fresh deps per call. Merges child usage into parent turn on success.
    Raises ModelRetry on any failure — no partial accounting on failure.
    """
    try:
        result = await agent.run(
            prompt,
            deps=make_subagent_deps(ctx.deps),
            usage_limits=UsageLimits(request_limit=budget),
            model_settings=model_settings,
            metadata={"session_id": ctx.deps.session.session_id},
        )
    except Exception as exc:
        raise ModelRetry(error_msg) from exc
    usage = result.usage()
    run_id = result.run_id
    _merge_turn_usage(ctx, usage)
    # Snapshot usage AFTER merge — decouples attempt_1.usage from turn_usage
    # so attempt_1.usage.requests stays stable during attempt_2.
    return SubagentAttempt(output=result.output, usage=copy(usage), run_id=run_id)


async def _run_subagent(
    ctx: RunContext[CoDeps], role_key: str, prompt: str, max_requests: int,
    *, domains: list[str] | None = None, inputs: list[str] | None = None,
) -> ToolReturn:
    """Common dispatch function for all tool subagents."""
    cfg = SUBAGENT_ROLES[role_key]
    if max_requests < 1:
        max_requests = getattr(ctx.deps.config, cfg.max_requests_key)

    registry = ctx.deps.model_registry
    if not registry or not registry.is_configured(cfg.role):
        raise ModelRetry(cfg.guard_msg)

    rm = registry.get(cfg.role, ResolvedModel(model=ctx.model, settings=None))
    model_name = str(rm.model)
    request_limit = max_requests
    agent = cfg.factory(rm)

    scoped_prompt = prompt
    if cfg.input_prepend and inputs:
        scoped_prompt = "Context:\n" + "\n".join(inputs) + "\n\nQuestion: " + prompt
    if domains:
        scoped_prompt = f"{scoped_prompt}\nRestrict searches to these domains: {', '.join(domains)}"

    with _TRACER.start_as_current_span(f"subagent_{cfg.role}") as span:
        span.set_attribute("subagent.role", cfg.role)
        span.set_attribute("subagent.model", model_name)
        span.set_attribute("subagent.request_limit", request_limit)
        attempt_1 = await _run_subagent_attempt(
            agent, scoped_prompt, ctx, max_requests, rm.settings, cfg.error_msg,
        )
        data = attempt_1.output
        requests_used = attempt_1.usage.requests

        if cfg.retry_on_empty:
            remaining = max_requests - attempt_1.usage.requests
            if remaining > 0 and (not data.summary or not data.sources):
                retry_query = f"The previous search returned no results. Try with different keywords: {prompt} (alternative framing)."
                attempt_2 = await _run_subagent_attempt(
                    agent, retry_query, ctx, remaining, rm.settings,
                    cfg.error_msg.replace("failed", "retry failed"),
                )
                data = attempt_2.output
                requests_used = attempt_1.usage.requests + attempt_2.usage.requests
            if not data.summary or not data.sources:
                data = data.model_copy(update={
                    "confidence": 0.0,
                    "summary": data.summary or "No results found despite multiple searches.",
                })

        span.set_attribute("subagent.requests_used", requests_used)

    scope = prompt[:ctx.deps.config.subagent_scope_chars]
    display, extra_meta = _format_output(
        role_key, data, scope, cfg.role, model_name, requests_used, request_limit,
    )
    return tool_output(
        display, **extra_meta, role=cfg.role, model_name=model_name,
        requests_used=requests_used, request_limit=request_limit,
        scope=scope, run_id=attempt_1.run_id,
    )


async def run_coding_subagent(
    ctx: RunContext[CoDeps], task: str, max_requests: int = 0,
) -> ToolReturn:
    """Delegate a coding analysis task to a read-only coder sub-agent.

    Args:
        task: The analysis task to investigate.
        max_requests: Max LLM requests (0 = config default).
    """
    return await _run_subagent(ctx, "coding", task, max_requests)


async def run_research_subagent(
    ctx: RunContext[CoDeps], query: str,
    domains: list[str] | None = None, max_requests: int = 0,
) -> ToolReturn:
    """Delegate a research task to a web-search sub-agent.

    Args:
        query: Research question or topic.
        domains: Restrict search to these domains.
        max_requests: Max LLM requests (0 = config default).
    """
    return await _run_subagent(ctx, "research", query, max_requests, domains=domains)


async def run_analysis_subagent(
    ctx: RunContext[CoDeps], question: str,
    inputs: list[str] | None = None, max_requests: int = 0,
) -> ToolReturn:
    """Delegate a knowledge-base analysis task to a read-only sub-agent.

    Args:
        question: The analysis question to investigate.
        inputs: Context strings to prepend to the question.
        max_requests: Max LLM requests (0 = config default).
    """
    return await _run_subagent(ctx, "analysis", question, max_requests, inputs=inputs)


async def run_reasoning_subagent(
    ctx: RunContext[CoDeps], problem: str, max_requests: int = 0,
) -> ToolReturn:
    """Delegate a structured reasoning task to a thinking sub-agent (no tools).

    Args:
        problem: The problem or question to reason about.
        max_requests: Max LLM requests (0 = config default).
    """
    return await _run_subagent(ctx, "reasoning", problem, max_requests)
