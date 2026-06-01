"""Delegation tools — thin spec-driven wrappers around task agents.

web_research defines a TaskAgentSpec and drives run_attempt inside its own
wrapper. It retains a retry-on-empty loop because both attempts must share a
single outer OTel span (single-span retry topology).
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn

from co_cli.agent.run import MAX_AGENT_DEPTH, merge_delegation_usage, run_attempt
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


# --- Specs ---


WEB_RESEARCH_SPEC = TaskAgentSpec(
    name="web_research",
    instructions=_researcher_instructions,
    tool_names=("web_fetch", "web_search"),
    output_type=AgentOutput,
    default_budget=10,
    error_message="Research agent failed — handle this task directly.",
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
    from memory or the knowledge base — use web_fetch or memory_search
    directly instead.

    Returns the agent's findings as a text result. Automatically retries once
    if the first search returns empty.

    Args:
        query: Research question or topic.
        domains: Restrict the agent's web searches to these domains (default None = search the entire web).
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

    output, usage_1, run_id = await run_attempt(
        WEB_RESEARCH_SPEC, ctx, scoped_prompt, budget, child_deps
    )
    merge_delegation_usage(ctx, usage_1)
    requests_used = usage_1.requests

    remaining = budget - usage_1.requests
    if remaining > 0 and not output.result.strip():
        retry_query = (
            f"The previous search returned no results. "
            f"Try with different keywords: {query} (alternative framing)."
        )
        output_2, usage_2, _ = await run_attempt(
            WEB_RESEARCH_SPEC, ctx, retry_query, remaining, child_deps
        )
        merge_delegation_usage(ctx, usage_2)
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
