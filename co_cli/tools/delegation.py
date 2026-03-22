"""Tools for delegating focused tasks to read-only sub-agents."""

from opentelemetry import trace as otel_trace
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import UsageLimits

from co_cli._model_factory import ResolvedModel
from co_cli.config import ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS, ROLE_REASONING
from co_cli.deps import CoDeps, make_subagent_deps
from co_cli.tools._result import ToolResult, make_result

_TRACER = otel_trace.get_tracer("co-cli.delegation")


async def delegate_coder(
    ctx: RunContext[CoDeps],
    task: str,
    max_requests: int = 10,
) -> ToolResult:
    """Delegate a coding analysis task to a read-only sub-agent.

    The coder sub-agent has access to list_directory, read_file, and
    find_in_files — no write access, no shell, no network. Use this for
    investigation tasks: understanding a codebase, finding where something
    is implemented, summarizing a module's purpose.

    Args:
        task: Natural language description of the analysis task.
        max_requests: Maximum LLM requests the sub-agent may make (default 10).

    Raises:
        ModelRetry: When max_requests < 1.
    """
    if max_requests < 1:
        raise ModelRetry("max_requests must be at least 1")

    from co_cli.tools._delegation_agents import make_coder_agent

    registry = ctx.deps.services.model_registry
    if not registry or not registry.is_configured(ROLE_CODING):
        raise ModelRetry("Coding sub-agent is unavailable — handle this task directly.")
    rm = registry.get(ROLE_CODING, ResolvedModel(model=ctx.model, settings=None))
    model_name = str(rm.model)
    role = ROLE_CODING
    request_limit = max_requests
    agent = make_coder_agent(rm)
    with _TRACER.start_as_current_span(f"delegate_{role}") as span:
        span.set_attribute("delegation.role", role)
        span.set_attribute("delegation.model", model_name)
        span.set_attribute("delegation.request_limit", request_limit)
        try:
            result = await agent.run(
                task,
                deps=make_subagent_deps(ctx.deps),
                usage=ctx.usage,
                usage_limits=UsageLimits(request_limit=max_requests),
                model_settings=rm.settings,
            )
        except Exception as exc:
            raise ModelRetry(f"Coding sub-agent failed: {exc} — handle this task directly.") from exc
        if ctx.deps.runtime.turn_usage is None:
            ctx.deps.runtime.turn_usage = result.usage()
        else:
            ctx.deps.runtime.turn_usage.incr(result.usage())
        requests_used = result.usage().requests
        span.set_attribute("delegation.requests_used", requests_used)
        data = result.output
    display = f"Coder analysis complete.\n{data.summary}\n[{role} · {model_name} · {requests_used}/{request_limit} req]"
    return make_result(
        display,
        summary=data.summary,
        diff_preview=data.diff_preview,
        files_touched=data.files_touched,
        confidence=data.confidence,
        role=role,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=request_limit,
    )


async def delegate_research(
    ctx: RunContext[CoDeps],
    query: str,
    domains: list[str] | None = None,
    max_requests: int = 8,
) -> ToolResult:
    """Delegate a research task to a focused sub-agent (web_search + web_fetch only).

    The research sub-agent searches the web and synthesizes a grounded summary
    with sources. Use this for factual lookups, documentation searches, and
    multi-source synthesis tasks. Does NOT perform write operations or save memories.

    Returns a dict with:
    - display: formatted summary with sources — show directly to the user
    - summary: research summary text
    - sources: list of source URLs used
    - confidence: 0.0–1.0 (0.0 if no results found after retry)

    Args:
        query: Research question or topic to investigate.
        domains: Restrict web search to these domains (e.g. ["docs.python.org"]).
        max_requests: Maximum LLM requests the sub-agent may make (default 8).

    Raises:
        ModelRetry: When max_requests < 1.
    """
    if max_requests < 1:
        raise ModelRetry("max_requests must be at least 1")

    policy = ctx.deps.config.web_policy
    if policy.search != "allow" or policy.fetch != "allow":
        raise ModelRetry(
            "Research sub-agent requires unrestricted web access; "
            "web_policy is not 'allow'. Handle this task directly."
        )

    from co_cli.tools._delegation_agents import make_research_agent

    registry = ctx.deps.services.model_registry
    if not registry or not registry.is_configured(ROLE_RESEARCH):
        raise ModelRetry("Research sub-agent is unavailable — handle this task directly.")
    rm = registry.get(ROLE_RESEARCH, ResolvedModel(model=ctx.model, settings=None))
    model_name = str(rm.model)
    role = ROLE_RESEARCH
    request_limit = max_requests
    sub_deps = make_subagent_deps(ctx.deps)
    agent = make_research_agent(rm)
    scoped_query = query if not domains else f"{query}\nRestrict searches to these domains: {', '.join(domains)}"
    with _TRACER.start_as_current_span(f"delegate_{role}") as span:
        span.set_attribute("delegation.role", role)
        span.set_attribute("delegation.model", model_name)
        span.set_attribute("delegation.request_limit", request_limit)
        try:
            result = await agent.run(
                scoped_query,
                deps=sub_deps,
                usage=ctx.usage,
                usage_limits=UsageLimits(request_limit=max_requests),
                model_settings=rm.settings,
            )
        except Exception as exc:
            raise ModelRetry(f"Research sub-agent failed: {exc} — handle this task directly.") from exc
        if ctx.deps.runtime.turn_usage is None:
            ctx.deps.runtime.turn_usage = result.usage()
        else:
            ctx.deps.runtime.turn_usage.incr(result.usage())
        data = result.output

        retry_result = None
        # Empty-result retry: rephrased query when budget remains and result is empty
        remaining = max_requests - result.usage().requests
        if remaining > 0 and (not data.summary or not data.sources):
            retry_query = f"The previous search returned no results. Try with different keywords: {query} (alternative framing)."
            retry_result = await agent.run(
                retry_query,
                deps=sub_deps,
                usage=ctx.usage,
                usage_limits=UsageLimits(request_limit=remaining),
                model_settings=rm.settings,
            )
            ctx.deps.runtime.turn_usage.incr(retry_result.usage())
            data = retry_result.output
        # Fallback: if result still empty (retry skipped or returned nothing), mark confidence=0.0
        if not data.summary or not data.sources:
            data = data.model_copy(update={"confidence": 0.0, "summary": data.summary or "No results found despite multiple searches."})

        requests_used = result.usage().requests + (retry_result.usage().requests if retry_result is not None else 0)
        span.set_attribute("delegation.requests_used", requests_used)
    sources_text = "\n".join(f"- {s}" for s in data.sources) if data.sources else "No sources"
    display = f"{data.summary}\n\nSources:\n{sources_text}\n[{role} · {model_name} · {requests_used}/{request_limit} req]"
    return make_result(
        display,
        summary=data.summary,
        sources=data.sources,
        confidence=data.confidence,
        role=role,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=request_limit,
    )


async def delegate_analysis(
    ctx: RunContext[CoDeps],
    question: str,
    inputs: list[str] | None = None,
    max_requests: int = 8,
) -> ToolResult:
    """Delegate a knowledge-base and Drive analysis task to a read-only sub-agent.

    The analysis sub-agent has access to search_knowledge and search_drive_files
    only — no write tools, no shell, no network. Use this for synthesis, comparison,
    and evaluation tasks against internal knowledge and Drive documents.

    Returns a dict with:
    - display: formatted conclusion with evidence — show directly to the user
    - conclusion: the sub-agent's conclusion
    - evidence: list of supporting evidence strings
    - reasoning: the sub-agent's reasoning chain

    Args:
        question: The analysis question or task to investigate.
        inputs: Optional context strings to prepend to the question.
        max_requests: Maximum LLM requests the sub-agent may make (default 8).

    Raises:
        ModelRetry: When max_requests < 1.
    """
    if max_requests < 1:
        raise ModelRetry("max_requests must be at least 1")

    # No web_policy gate here: analysis sub-agent uses search_knowledge and
    # search_drive_files only — no web tools. If Drive ever gets a policy
    # setting, the gate belongs here, mirroring delegate_research above.

    from co_cli.tools._delegation_agents import make_analysis_agent

    registry = ctx.deps.services.model_registry
    if not registry or not registry.is_configured(ROLE_ANALYSIS):
        raise ModelRetry("Analysis sub-agent is unavailable — handle this task directly.")
    rm = registry.get(ROLE_ANALYSIS, ResolvedModel(model=ctx.model, settings=None))
    model_name = str(rm.model)
    role = ROLE_ANALYSIS
    request_limit = max_requests
    scoped_question = question
    if inputs:
        scoped_question = "Context:\n" + "\n".join(inputs) + "\n\nQuestion: " + question

    agent = make_analysis_agent(rm)
    with _TRACER.start_as_current_span(f"delegate_{role}") as span:
        span.set_attribute("delegation.role", role)
        span.set_attribute("delegation.model", model_name)
        span.set_attribute("delegation.request_limit", request_limit)
        try:
            result = await agent.run(
                scoped_question,
                deps=make_subagent_deps(ctx.deps),
                usage=ctx.usage,
                usage_limits=UsageLimits(request_limit=max_requests),
                model_settings=rm.settings,
            )
        except Exception as exc:
            raise ModelRetry(f"Analysis sub-agent failed: {exc} — handle this task directly.") from exc
        if ctx.deps.runtime.turn_usage is None:
            ctx.deps.runtime.turn_usage = result.usage()
        else:
            ctx.deps.runtime.turn_usage.incr(result.usage())
        requests_used = result.usage().requests
        span.set_attribute("delegation.requests_used", requests_used)
        data = result.output
    evidence_text = "\n".join(f"- {e}" for e in data.evidence) if data.evidence else "No evidence"
    display = f"{data.conclusion}\n\nEvidence:\n{evidence_text}\n[{role} · {model_name} · {requests_used}/{request_limit} req]"
    return make_result(
        display,
        conclusion=data.conclusion,
        evidence=data.evidence,
        reasoning=data.reasoning,
        role=role,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=request_limit,
    )


async def delegate_think(
    ctx: RunContext[CoDeps],
    problem: str,
    max_requests: int = 5,
) -> ToolResult:
    """Delegate a structured reasoning task to a thinking sub-agent.

    The thinking sub-agent has NO tools — it reasons purely via the model's
    native extended thinking capability. Use this for problem decomposition,
    planning, and synthesis tasks that benefit from a dedicated reasoning pass.

    Returns a dict with:
    - display: formatted plan + steps + conclusion — show directly to the user
    - plan: high-level approach (1–3 sentences)
    - steps: ordered action steps
    - conclusion: synthesized answer or recommendation

    Args:
        problem: The problem or question to reason about.
        max_requests: Maximum LLM requests the sub-agent may make (default 5).

    Raises:
        ModelRetry: When max_requests < 1.
    """
    if max_requests < 1:
        raise ModelRetry("max_requests must be at least 1")

    from co_cli.tools._delegation_agents import make_thinking_agent

    registry = ctx.deps.services.model_registry
    if not registry or not registry.is_configured(ROLE_REASONING):
        raise ModelRetry("Thinking sub-agent is unavailable — handle this task directly.")
    rm = registry.get(ROLE_REASONING, ResolvedModel(model=ctx.model, settings=None))
    model_name = str(rm.model)
    role = ROLE_REASONING
    request_limit = max_requests
    agent = make_thinking_agent(rm)
    with _TRACER.start_as_current_span(f"delegate_{role}") as span:
        span.set_attribute("delegation.role", role)
        span.set_attribute("delegation.model", model_name)
        span.set_attribute("delegation.request_limit", request_limit)
        try:
            result = await agent.run(
                problem,
                deps=make_subagent_deps(ctx.deps),
                usage=ctx.usage,
                usage_limits=UsageLimits(request_limit=max_requests),
                model_settings=rm.settings,
            )
        except Exception as exc:
            raise ModelRetry(f"Thinking sub-agent failed: {exc} — handle this task directly.") from exc
        if ctx.deps.runtime.turn_usage is None:
            ctx.deps.runtime.turn_usage = result.usage()
        else:
            ctx.deps.runtime.turn_usage.incr(result.usage())
        requests_used = result.usage().requests
        span.set_attribute("delegation.requests_used", requests_used)
        data = result.output
    steps_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(data.steps))
    display = f"{data.plan}\n\nSteps:\n{steps_text}\n\nConclusion:\n{data.conclusion}\n[{role} · {model_name} · {requests_used}/{request_limit} req]"
    return make_result(
        display,
        plan=data.plan,
        steps=data.steps,
        conclusion=data.conclusion,
        role=role,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=request_limit,
    )
