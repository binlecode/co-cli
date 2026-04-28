"""Agent delegation tools — spawn focused sub-agents for scoped tasks."""

from copy import copy
from typing import Any

from opentelemetry import trace as otel_trace
from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from co_cli.deps import CoDeps, VisibilityPolicyEnum, fork_deps
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output

_TRACER = otel_trace.get_tracer("co-cli.agents")

# Maximum delegation depth — safety rail against accidental recursive delegation.
MAX_AGENT_DEPTH: int = 2


class AgentOutput(BaseModel):
    """Output from a delegation agent."""

    result: str


def _merge_turn_usage(ctx: RunContext[CoDeps], usage: RunUsage) -> None:
    """Merge delegation agent usage into the parent turn's authoritative usage accumulator."""
    if ctx.deps.runtime.turn_usage is None:
        ctx.deps.runtime.turn_usage = usage
    else:
        ctx.deps.runtime.turn_usage.incr(usage)


async def _run_agent_attempt(
    agent: Any,
    prompt: str,
    ctx: RunContext[CoDeps],
    budget: int,
    model_settings: Any,
    error_msg: str,
    child_deps: CoDeps,
) -> tuple[Any, RunUsage, str]:
    """Run one agent attempt. Returns (output, usage, run_id).

    Merges child usage into parent turn on success.
    Raises ModelRetry on any failure — no partial accounting on failure.
    """
    try:
        result = await agent.run(
            prompt,
            deps=child_deps,
            usage_limits=UsageLimits(request_limit=budget),
            model_settings=model_settings,
            metadata={"session_id": ctx.deps.session.session_path.stem[-8:]},
        )
    except Exception as exc:
        raise ModelRetry(error_msg) from exc
    usage = result.usage()
    run_id = result.run_id
    _merge_turn_usage(ctx, usage)
    return result.output, copy(usage), run_id


async def _delegate_agent(
    ctx: RunContext[CoDeps],
    task: str,
    agent: Any,
    budget: int,
    model_settings: ModelSettings | None,
    role_key: str,
    *,
    _precomputed: tuple[AgentOutput, int, str] | None = None,
) -> ToolReturn:
    """Run a delegation agent with shared orchestration: OTel span, fork_deps, usage merge.

    Pass _precomputed=(output, requests_used, run_id) to skip agent execution and
    format a pre-computed result (used by web_research's retry path, which
    manages its own span to cover both the primary attempt and any retry).
    """
    model_name = str(agent.model)
    if _precomputed is not None:
        output, requests_used, run_id = _precomputed
    else:
        child_deps = fork_deps(ctx.deps)
        child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback
        with _TRACER.start_as_current_span(role_key) as span:
            span.set_attribute("agent.role", role_key)
            span.set_attribute("agent.model", model_name)
            span.set_attribute("agent.request_limit", budget)
            output, usage, run_id = await _run_agent_attempt(
                agent,
                task,
                ctx,
                budget,
                model_settings,
                f"{role_key.capitalize()} agent failed — handle this task directly.",
                child_deps,
            )
            span.set_attribute("agent.requests_used", usage.requests)
        requests_used = usage.requests
    display = f"{output.result}\n[{role_key} · {model_name} · {requests_used}/{budget} req]"
    return tool_output(
        display,
        ctx=ctx,
        role=role_key,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=budget,
        run_id=run_id,
    )


# --- Instruction builders ---


def _researcher_instructions(deps: CoDeps) -> str:
    return (
        "You are a read-only research agent. "
        "Search the web and fetch pages to answer the query. "
        "Synthesize what you find into a grounded summary with sources. "
        "Return your findings in result. Include: a summary of what you found, the source URLs, "
        "and a confidence note (high/medium/low). "
        "If you found nothing after exhausting available searches, say so clearly in result."
    )


def _analyst_instructions(deps: CoDeps) -> str:
    active_sources = []
    if deps.knowledge_store is not None:
        active_sources.append("knowledge base")
    if deps.config.google_credentials_path:
        active_sources.append("Google Drive")
    sources_note = (
        f"Active knowledge sources: {', '.join(active_sources)}."
        if active_sources
        else "No configured knowledge sources available — reason from provided context."
    )
    return (
        f"You are a read-only analysis agent. "
        f"{sources_note} "
        f"Use the available search tools to gather evidence, then compare, evaluate, "
        f"and synthesize the provided inputs. "
        f"Return your analysis in result. Include: a clear conclusion, supporting evidence, "
        f"and the reasoning behind your conclusion."
    )


def _reasoner_instructions(deps: CoDeps) -> str:
    return (
        "You are a reasoning agent. "
        "Decompose the problem, reason step-by-step, and return a structured result. "
        "Return your reasoning in result. Include: a high-level approach (1–3 sentences), "
        "ordered action steps, and a synthesized answer or recommendation."
    )


# --- Delegation tools ---


@agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, is_concurrent_safe=True)
async def web_research(
    ctx: RunContext[CoDeps],
    query: str,
    domains: list[str] | None = None,
    max_requests: int = 0,
) -> ToolReturn:
    """Delegate web research to a search-and-fetch agent with web tools.

    When to use: questions that require searching the web, reading external
    pages, and synthesizing findings — e.g. "what are the latest changes in
    library X?" or "compare pricing of service A vs B". Give a specific,
    self-contained research question.

    When NOT to use: a single URL fetch or a factual question you can answer
    from memory or the knowledge base — use web_fetch or memory_search
    directly instead.

    Returns the agent's findings as a text result. Automatically retries once
    if the first search returns empty.

    Args:
        query: Research question or topic.
        domains: Restrict search to these domains.
        max_requests: Max LLM requests (0 = config default).
    """
    if ctx.deps.runtime.agent_depth >= MAX_AGENT_DEPTH:
        raise ModelRetry(
            f"Delegation depth limit reached ({MAX_AGENT_DEPTH}). Handle this task directly."
        )
    if not ctx.deps.model:
        raise ModelRetry("Research agent is unavailable — handle this task directly.")

    from co_cli.agent.core import build_agent
    from co_cli.tools.web.fetch import web_fetch
    from co_cli.tools.web.search import web_search

    _default_max_requests = 10
    budget = max_requests or _default_max_requests
    model_obj = ctx.deps.model.model

    scoped_prompt = query
    if domains:
        scoped_prompt = (
            f"{scoped_prompt}\nRestrict searches to these domains: {', '.join(domains)}"
        )

    agent = build_agent(
        config=ctx.deps.config,
        model=model_obj,
        instructions=_researcher_instructions(ctx.deps),
        tool_fns=[web_search, web_fetch],
        output_type=AgentOutput,
    )

    child_deps = fork_deps(ctx.deps)
    child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback

    # Researcher manages its own span to cover both the primary attempt and any retry.
    with _TRACER.start_as_current_span("web_research") as span:
        span.set_attribute("agent.role", "researcher")
        span.set_attribute("agent.model", str(model_obj))
        span.set_attribute("agent.request_limit", budget)
        output, usage_1, run_id = await _run_agent_attempt(
            agent,
            scoped_prompt,
            ctx,
            budget,
            ctx.deps.model.settings,
            "Research agent failed — handle this task directly.",
            child_deps,
        )
        requests_used = usage_1.requests

        # retry_on_empty: if first attempt returned nothing, retry once with rephrased query
        remaining = budget - usage_1.requests
        if remaining > 0 and not output.result.strip():
            retry_query = (
                f"The previous search returned no results. "
                f"Try with different keywords: {query} (alternative framing)."
            )
            output_2, usage_2, _ = await _run_agent_attempt(
                agent,
                retry_query,
                ctx,
                remaining,
                ctx.deps.model.settings,
                "Research agent retry failed — handle this task directly.",
                child_deps,
            )
            output = output_2
            requests_used = usage_1.requests + usage_2.requests
        if not output.result.strip():
            output = AgentOutput(result="No results found despite multiple searches.")

        span.set_attribute("agent.requests_used", requests_used)

    return await _delegate_agent(
        ctx,
        query,
        agent,
        budget,
        ctx.deps.model.settings,
        "web_research",
        _precomputed=(output, requests_used, run_id),
    )


@agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, is_concurrent_safe=True)
async def knowledge_analyze(
    ctx: RunContext[CoDeps],
    question: str,
    inputs: list[str] | None = None,
    max_requests: int = 0,
) -> ToolReturn:
    """Delegate knowledge-base analysis to an agent with memory and Drive search.

    When to use: synthesis, comparison, or evaluation tasks that require
    searching the knowledge base and/or Google Drive — e.g. "compare our
    auth design to the spec" or "what do our notes say about X?". Pass
    context via inputs when the agent needs prior results to reason over.

    When NOT to use: a single keyword search against the knowledge base —
    use memory_search directly instead.

    Returns the agent's findings as a text result.

    Args:
        question: The analysis question to investigate.
        inputs: Context strings to prepend to the question.
        max_requests: Max LLM requests (0 = config default).
    """
    if ctx.deps.runtime.agent_depth >= MAX_AGENT_DEPTH:
        raise ModelRetry(
            f"Delegation depth limit reached ({MAX_AGENT_DEPTH}). Handle this task directly."
        )
    if not ctx.deps.model:
        raise ModelRetry("Analysis agent is unavailable — handle this task directly.")

    from co_cli.agent.core import build_agent
    from co_cli.tools.google.drive import google_drive_search
    from co_cli.tools.memory.recall import memory_search

    _default_max_requests = 8
    budget = max_requests or _default_max_requests

    scoped_prompt = question
    if inputs:
        scoped_prompt = "Context:\n" + "\n".join(inputs) + "\n\nQuestion: " + question

    agent = build_agent(
        config=ctx.deps.config,
        model=ctx.deps.model.model,
        instructions=_analyst_instructions(ctx.deps),
        tool_fns=[memory_search, google_drive_search],
        output_type=AgentOutput,
    )
    return await _delegate_agent(
        ctx, scoped_prompt, agent, budget, ctx.deps.model.settings, "knowledge_analyze"
    )


@agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, is_concurrent_safe=True)
async def reason(
    ctx: RunContext[CoDeps],
    problem: str,
    max_requests: int = 0,
) -> ToolReturn:
    """Delegate structured reasoning to a tool-free thinking agent.

    When to use: problems that benefit from dedicated step-by-step reasoning
    — planning, trade-off analysis, problem decomposition, or multi-constraint
    decisions. The agent has no tools; it reasons purely via the model's
    native thinking capability. Give a complete problem statement.

    When NOT to use: tasks that require reading files, searching the web, or
    querying the knowledge base — those need the coder, researcher, or analyst
    agents respectively.

    Returns the agent's findings as a text result.

    Args:
        problem: The problem or question to reason about.
        max_requests: Max LLM requests (0 = config default).
    """
    if ctx.deps.runtime.agent_depth >= MAX_AGENT_DEPTH:
        raise ModelRetry(
            f"Delegation depth limit reached ({MAX_AGENT_DEPTH}). Handle this task directly."
        )
    if not ctx.deps.model:
        raise ModelRetry("Reasoning agent is unavailable — handle this task directly.")

    from co_cli.agent.core import build_agent

    _default_max_requests = 3
    budget = max_requests or _default_max_requests
    # Reasoning agent uses the main model's base settings (config-derived, may include thinking tokens)
    task_settings = ctx.deps.model.settings
    agent = build_agent(
        config=ctx.deps.config,
        model=ctx.deps.model.model,
        instructions=_reasoner_instructions(ctx.deps),
        tool_fns=None,
        output_type=AgentOutput,
    )
    return await _delegate_agent(ctx, problem, agent, budget, task_settings, "reason")
