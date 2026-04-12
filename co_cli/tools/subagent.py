"""Tools for running focused tasks via purpose-built sub-agents."""

from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from typing import Any, NamedTuple

from opentelemetry import trace as otel_trace
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.deps import CoDeps, make_subagent_deps
from co_cli.memory.prompt_builders import build_save_user_prompt
from co_cli.memory.save_agent import SaveMemoryAgentOutput as _SaveMemoryAgentOutput
from co_cli.memory.save_agent import _save_memory_agent
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

    factory: Callable[[Any], Agent[CoDeps, Any]]
    max_requests_key: str
    error_msg: str
    guard_msg: str
    retry_on_empty: bool = False
    input_prepend: bool = False


SUBAGENT_ROLES: dict[str, SubagentRoleConfig] = {
    "coding": SubagentRoleConfig(
        factory=make_coder_agent,
        max_requests_key="max_requests_coder",
        error_msg="Coding sub-agent failed — handle this task directly.",
        guard_msg="Coding sub-agent is unavailable — handle this task directly.",
    ),
    "research": SubagentRoleConfig(
        factory=make_research_agent,
        max_requests_key="max_requests_research",
        error_msg="Research sub-agent failed — handle this task directly.",
        guard_msg="Research sub-agent is unavailable — handle this task directly.",
        retry_on_empty=True,
    ),
    "analysis": SubagentRoleConfig(
        factory=make_analysis_agent,
        max_requests_key="max_requests_analysis",
        error_msg="Analysis sub-agent failed — handle this task directly.",
        guard_msg="Analysis sub-agent is unavailable — handle this task directly.",
        input_prepend=True,
    ),
    "reasoning": SubagentRoleConfig(
        factory=make_thinking_agent,
        max_requests_key="max_requests_thinking",
        error_msg="Thinking sub-agent failed — handle this task directly.",
        guard_msg="Thinking sub-agent is unavailable — handle this task directly.",
    ),
}


def _get_task_settings(role_key: str, deps: CoDeps) -> ModelSettings | None:
    """Return the ModelSettings for a subagent role.

    Reasoning subagent uses the main model's base settings (quirks-derived).
    All other subagents use NOREASON_SETTINGS.
    """
    if role_key == "reasoning":
        return deps.model.settings if deps.model else None
    return NOREASON_SETTINGS


def _format_output(
    role_key: str,
    data: Any,
    scope: str,
    role: str,
    model_name: str,
    requests_used: int,
    request_limit: int,
) -> tuple[str, dict[str, Any]]:
    """Format subagent output for display and extract metadata kwargs per role."""
    footer = f"[{role} · {model_name} · {requests_used}/{request_limit} req]"
    match role_key:
        case "coding":
            display = f"Scope: {scope}\nCoder analysis complete.\n{data.summary}\n{footer}"
            meta = dict(
                summary=data.summary,
                diff_preview=data.diff_preview,
                files_touched=data.files_touched,
                confidence=data.confidence,
            )
        case "research":
            src = "\n".join(f"- {s}" for s in data.sources) if data.sources else "No sources"
            display = f"Scope: {scope}\n{data.summary}\n\nSources:\n{src}\n{footer}"
            meta = dict(summary=data.summary, sources=data.sources, confidence=data.confidence)
        case "analysis":
            ev = "\n".join(f"- {e}" for e in data.evidence) if data.evidence else "No evidence"
            display = f"Scope: {scope}\n{data.conclusion}\n\nEvidence:\n{ev}\n{footer}"
            meta = dict(
                conclusion=data.conclusion, evidence=data.evidence, reasoning=data.reasoning
            )
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
    agent: Any,
    prompt: str,
    ctx: RunContext[CoDeps],
    budget: int,
    model_settings: Any,
    error_msg: str,
    model: Any = None,
) -> SubagentAttempt:
    """Run one subagent attempt with a fresh usage context.

    Creates fresh deps per call. Merges child usage into parent turn on success.
    Raises ModelRetry on any failure — no partial accounting on failure.
    model: when non-None, passed to agent.run() for singleton agents that have no baked model.
    """
    try:
        result = await agent.run(
            prompt,
            deps=make_subagent_deps(ctx.deps),
            usage_limits=UsageLimits(request_limit=budget),
            model_settings=model_settings,
            metadata={"session_id": ctx.deps.session.session_id},
            **({"model": model} if model is not None else {}),
        )
    except Exception as exc:
        raise ModelRetry(error_msg) from exc
    usage = result.usage()
    run_id = result.run_id
    _merge_turn_usage(ctx, usage)
    return SubagentAttempt(output=result.output, usage=copy(usage), run_id=run_id)


async def _run_subagent(
    ctx: RunContext[CoDeps],
    role_key: str,
    prompt: str,
    max_requests: int,
    *,
    domains: list[str] | None = None,
    inputs: list[str] | None = None,
) -> ToolReturn:
    """Common dispatch function for all tool subagents."""
    cfg = SUBAGENT_ROLES[role_key]
    if max_requests < 1:
        max_requests = getattr(ctx.deps.config.subagent, cfg.max_requests_key)

    if not ctx.deps.model:
        raise ModelRetry(cfg.guard_msg)

    model_obj = ctx.deps.model.model
    model_name = str(model_obj)
    task_settings = _get_task_settings(role_key, ctx.deps)
    request_limit = max_requests
    agent = cfg.factory(model_obj)

    scoped_prompt = prompt
    if cfg.input_prepend and inputs:
        scoped_prompt = "Context:\n" + "\n".join(inputs) + "\n\nQuestion: " + prompt
    if domains:
        scoped_prompt = (
            f"{scoped_prompt}\nRestrict searches to these domains: {', '.join(domains)}"
        )

    with _TRACER.start_as_current_span(f"subagent_{role_key}") as span:
        span.set_attribute("subagent.role", role_key)
        span.set_attribute("subagent.model", model_name)
        span.set_attribute("subagent.request_limit", request_limit)
        attempt_1 = await _run_subagent_attempt(
            agent,
            scoped_prompt,
            ctx,
            max_requests,
            task_settings,
            cfg.error_msg,
        )
        data = attempt_1.output
        requests_used = attempt_1.usage.requests

        if cfg.retry_on_empty:
            remaining = max_requests - attempt_1.usage.requests
            if remaining > 0 and (not data.summary or not data.sources):
                retry_query = f"The previous search returned no results. Try with different keywords: {prompt} (alternative framing)."
                attempt_2 = await _run_subagent_attempt(
                    agent,
                    retry_query,
                    ctx,
                    remaining,
                    task_settings,
                    cfg.error_msg.replace("failed", "retry failed"),
                )
                data = attempt_2.output
                requests_used = attempt_1.usage.requests + attempt_2.usage.requests
            if not data.summary or not data.sources:
                data = data.model_copy(
                    update={
                        "confidence": 0.0,
                        "summary": data.summary or "No results found despite multiple searches.",
                    }
                )

        span.set_attribute("subagent.requests_used", requests_used)

    scope = prompt[: ctx.deps.config.subagent.scope_chars]
    display, extra_meta = _format_output(
        role_key,
        data,
        scope,
        role_key,
        model_name,
        requests_used,
        request_limit,
    )
    return tool_output(
        display,
        ctx=ctx,
        **extra_meta,
        role=role_key,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=request_limit,
        scope=scope,
        run_id=attempt_1.run_id,
    )


async def run_coding_subagent(
    ctx: RunContext[CoDeps],
    task: str,
    max_requests: int = 0,
) -> ToolReturn:
    """Delegate codebase analysis to a read-only coder sub-agent with file tools.

    When to use: multi-file code investigation, architecture tracing, impact
    analysis, or any task that requires reading and cross-referencing several
    files to produce a conclusion. Give a complete, concrete task description
    so the subagent can work autonomously.

    When NOT to use: reading a single file, listing a directory, or grepping
    for a pattern — use the primitive file tools directly instead.

    Returns a CoderOutput with summary, diff_preview, files_touched, and
    confidence (0.0-1.0). Trust the result unless the confidence is low.

    Args:
        task: The analysis task to investigate.
        max_requests: Max LLM requests (0 = config default).
    """
    return await _run_subagent(ctx, "coding", task, max_requests)


async def run_research_subagent(
    ctx: RunContext[CoDeps],
    query: str,
    domains: list[str] | None = None,
    max_requests: int = 0,
) -> ToolReturn:
    """Delegate web research to a search-and-fetch sub-agent with web tools.

    When to use: questions that require searching the web, reading external
    pages, and synthesizing findings — e.g. "what are the latest changes in
    library X?" or "compare pricing of service A vs B". Give a specific,
    self-contained research question.

    When NOT to use: a single URL fetch or a factual question you can answer
    from memory or the knowledge base — use web_fetch or search_knowledge
    directly instead.

    Returns a ResearchOutput with summary, sources (URLs), and confidence
    (0.0-1.0). Automatically retries once if the first search returns empty.

    Args:
        query: Research question or topic.
        domains: Restrict search to these domains.
        max_requests: Max LLM requests (0 = config default).
    """
    return await _run_subagent(ctx, "research", query, max_requests, domains=domains)


async def run_analysis_subagent(
    ctx: RunContext[CoDeps],
    question: str,
    inputs: list[str] | None = None,
    max_requests: int = 0,
) -> ToolReturn:
    """Delegate knowledge-base analysis to a sub-agent with memory and Drive search.

    When to use: synthesis, comparison, or evaluation tasks that require
    searching the knowledge base and/or Google Drive — e.g. "compare our
    auth design to the spec" or "what do our notes say about X?". Pass
    context via inputs when the subagent needs prior results to reason over.

    When NOT to use: a single keyword search against the knowledge base —
    use search_knowledge directly instead.

    Returns an AnalysisOutput with conclusion, evidence (list of supporting
    points), and reasoning (the chain of thought behind the conclusion).

    Args:
        question: The analysis question to investigate.
        inputs: Context strings to prepend to the question.
        max_requests: Max LLM requests (0 = config default).
    """
    return await _run_subagent(ctx, "analysis", question, max_requests, inputs=inputs)


async def run_reasoning_subagent(
    ctx: RunContext[CoDeps],
    problem: str,
    max_requests: int = 0,
) -> ToolReturn:
    """Delegate structured reasoning to a tool-free thinking sub-agent.

    When to use: problems that benefit from dedicated step-by-step reasoning
    — planning, trade-off analysis, problem decomposition, or multi-constraint
    decisions. The subagent has no tools; it reasons purely via the model's
    native thinking capability. Give a complete problem statement.

    When NOT to use: tasks that require reading files, searching the web, or
    querying the knowledge base — those need the coder, research, or analysis
    subagents respectively.

    Returns a ThinkingOutput with plan (high-level approach), steps (ordered
    action items), and conclusion (synthesized answer or recommendation).

    Args:
        problem: The problem or question to reason about.
        max_requests: Max LLM requests (0 = config default).
    """
    return await _run_subagent(ctx, "reasoning", problem, max_requests)


async def _run_save_memory_agent(
    ctx: RunContext[CoDeps],
    instruction: str,
    max_requests: int,
) -> ToolReturn:
    """Run the save_memory subagent with a natural-language instruction.

    Not registered in SUBAGENT_ROLES — memory dispatch has a different output
    shape and uses a module-level singleton with model passed at run() time.
    Called by the save_memory tool (TASK-3).
    """
    if max_requests < 1:
        max_requests = ctx.deps.config.subagent.max_requests_memory
    if not ctx.deps.model:
        raise ModelRetry("Memory sub-agent is unavailable — handle directly.")
    attempt = await _run_subagent_attempt(
        _save_memory_agent,
        build_save_user_prompt(instruction),
        ctx,
        max_requests,
        NOREASON_SETTINGS,
        "Memory sub-agent failed — handle this task directly.",
        model=ctx.deps.model.model,
    )
    data: _SaveMemoryAgentOutput = attempt.output
    display = f"Memory write complete.\n{data.summary}\nFiles: {', '.join(data.files_touched)}"
    return tool_output(
        display,
        ctx=ctx,
        summary=data.summary,
        files_touched=data.files_touched,
        actions=data.actions,
        confidence=data.confidence,
        requests_used=attempt.usage.requests,
        run_id=attempt.run_id,
    )
