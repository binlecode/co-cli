"""Delegation tools — thin spec-driven wrappers around task agents.

Each tool defines a TaskAgentSpec and delegates to run_in_turn. web_research
retains a retry-on-empty loop in the wrapper because both attempts must
share a single outer OTel span (single-span retry topology).
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.agent.run import MAX_AGENT_DEPTH, _merge_turn_usage, _run_attempt, run_in_turn
from co_cli.agent.spec import TaskAgentSpec
from co_cli.deps import CoDeps, VisibilityPolicyEnum, fork_deps
from co_cli.observability.tracing import current_span, trace
from co_cli.tools.agent_tool import agent_tool
from co_cli.tools.tool_io import tool_output


class AgentOutput(BaseModel):
    """Output from a delegation agent."""

    result: str


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
    if deps.memory_store is not None:
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


# --- Specs ---


WEB_RESEARCH_SPEC = TaskAgentSpec(
    name="web_research",
    instructions=_researcher_instructions,
    tool_names=("web_fetch", "web_search"),
    output_type=AgentOutput,
    default_budget=10,
    error_message="Research agent failed — handle this task directly.",
)


KNOWLEDGE_ANALYZE_SPEC = TaskAgentSpec(
    name="knowledge_analyze",
    instructions=_analyst_instructions,
    tool_names=(
        "knowledge_search",
        "google_drive_search",
        "google_drive_read",
        "obsidian_search",
        "obsidian_list",
        "obsidian_read",
    ),
    output_type=AgentOutput,
    default_budget=8,
    error_message="Analysis agent failed — handle this task directly.",
)


REASON_SPEC = TaskAgentSpec(
    name="reason",
    instructions=_reasoner_instructions,
    tool_names=(),
    output_type=AgentOutput,
    default_budget=3,
    error_message="Reasoning agent failed — handle this task directly.",
)


# --- Delegation tools ---


@agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, is_concurrent_safe=True)
@trace("co.web_research.retry_loop")
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
    from memory or the knowledge base — use web_fetch or knowledge_search
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

    budget = max_requests or WEB_RESEARCH_SPEC.default_budget
    model_obj = ctx.deps.model.model

    scoped_prompt = query
    if domains:
        scoped_prompt = (
            f"{scoped_prompt}\nRestrict searches to these domains: {', '.join(domains)}"
        )

    child_deps = fork_deps(ctx.deps)
    child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback

    span = current_span()
    span.set_attribute("agent.role", WEB_RESEARCH_SPEC.name)
    span.set_attribute("agent.model", str(model_obj))
    span.set_attribute("agent.request_limit", budget)

    output, usage_1, run_id = await _run_attempt(
        WEB_RESEARCH_SPEC, ctx, scoped_prompt, budget, child_deps
    )
    _merge_turn_usage(ctx, usage_1)
    requests_used = usage_1.requests

    remaining = budget - usage_1.requests
    if remaining > 0 and not output.result.strip():
        retry_query = (
            f"The previous search returned no results. "
            f"Try with different keywords: {query} (alternative framing)."
        )
        output_2, usage_2, _ = await _run_attempt(
            WEB_RESEARCH_SPEC, ctx, retry_query, remaining, child_deps
        )
        _merge_turn_usage(ctx, usage_2)
        output = output_2
        requests_used = usage_1.requests + usage_2.requests
    if not output.result.strip():
        output = AgentOutput(result="No results found despite multiple searches.")

    span.set_attribute("agent.requests_used", requests_used)

    display = (
        f"{output.result}\n[{WEB_RESEARCH_SPEC.name} · {model_obj} · {requests_used}/{budget} req]"
    )
    return tool_output(
        display,
        ctx=ctx,
        role=WEB_RESEARCH_SPEC.name,
        model_name=str(model_obj),
        requests_used=requests_used,
        request_limit=budget,
        run_id=run_id,
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
    use knowledge_search directly instead.

    Returns the agent's findings as a text result.

    Args:
        question: The analysis question to investigate.
        inputs: Context strings to prepend to the question.
        max_requests: Max LLM requests (0 = config default).
    """
    scoped_prompt = question
    if inputs:
        scoped_prompt = "Context:\n" + "\n".join(inputs) + "\n\nQuestion: " + question
    return await run_in_turn(
        KNOWLEDGE_ANALYZE_SPEC, ctx, scoped_prompt, budget=max_requests or None
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
    return await run_in_turn(REASON_SPEC, ctx, problem, budget=max_requests or None)
