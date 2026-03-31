"""Tools for running focused tasks via purpose-built sub-agents."""

from copy import copy
from typing import Any, NamedTuple

from opentelemetry import trace as otel_trace
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.usage import RunUsage, UsageLimits

from co_cli._model_factory import ResolvedModel
from co_cli.config import ROLE_ANALYSIS, ROLE_CODING, ROLE_REASONING, ROLE_RESEARCH
from co_cli.deps import CoDeps, make_subagent_deps
from co_cli.tools._result import ToolResult, make_result

_TRACER = otel_trace.get_tracer("co-cli.subagent")


class SubagentAttemptResult(NamedTuple):
    output: Any
    usage: RunUsage  # child-only snapshot — safe to read after turn_usage merge
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
) -> SubagentAttemptResult:
    """Run one subagent attempt with a fresh usage context (Mode 2).

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
            # no usage= argument — SDK creates a fresh RunUsage()
        )
    except Exception as exc:
        raise ModelRetry(error_msg) from exc
    usage = result.usage()
    run_id = result.run_id
    _merge_turn_usage(ctx, usage)
    # Snapshot usage AFTER merge. _merge_turn_usage may alias turn_usage = usage
    # (no copy when turn_usage is None). A later incr() on turn_usage mutates the
    # original RunUsage object in-place. The snapshot decouples SubagentAttemptResult.usage
    # from turn_usage so attempt_1.usage.requests stays stable during attempt_2.
    return SubagentAttemptResult(output=result.output, usage=copy(usage), run_id=run_id)


async def run_coder_subagent(
    ctx: RunContext[CoDeps],
    task: str,
    max_requests: int = 0,
) -> ToolResult:
    """Delegate a coding analysis task to a read-only sub-agent.

    The coder sub-agent has access to list_directory, read_file, and
    find_in_files — no write access, no shell, no network. Use this for
    investigation tasks: understanding a codebase, finding where something
    is implemented, summarizing a module's purpose.

    Args:
        task: Natural language description of the analysis task.
        max_requests: Maximum LLM requests the sub-agent may make (0 = use config default).

    Raises:
        ModelRetry: Never raised for max_requests; 0 resolves to the configured default.
    """
    if max_requests < 1:
        max_requests = ctx.deps.config.subagent_max_requests_coder

    from co_cli.tools._subagent_agents import make_coder_agent

    registry = ctx.deps.services.model_registry
    if not registry or not registry.is_configured(ROLE_CODING):
        raise ModelRetry("Coding sub-agent is unavailable — handle this task directly.")
    rm = registry.get(ROLE_CODING, ResolvedModel(model=ctx.model, settings=None))
    model_name = str(rm.model)
    role = ROLE_CODING
    request_limit = max_requests
    agent = make_coder_agent(rm)
    with _TRACER.start_as_current_span(f"subagent_{role}") as span:
        span.set_attribute("subagent.role", role)
        span.set_attribute("subagent.model", model_name)
        span.set_attribute("subagent.request_limit", request_limit)
        attempt = await _run_subagent_attempt(
            agent, task, ctx, max_requests, rm.settings,
            "Coding sub-agent failed — handle this task directly.",
        )
        requests_used = attempt.usage.requests
        span.set_attribute("subagent.requests_used", requests_used)
        data = attempt.output
    scope = task[:ctx.deps.config.subagent_scope_chars]
    display = f"Scope: {scope}\nCoder analysis complete.\n{data.summary}\n[{role} · {model_name} · {requests_used}/{request_limit} req]"
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
        scope=scope,
        run_id=attempt.run_id,
    )


async def run_research_subagent(
    ctx: RunContext[CoDeps],
    query: str,
    domains: list[str] | None = None,
    max_requests: int = 0,
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
        max_requests: Maximum LLM requests the sub-agent may make (0 = use config default).

    Raises:
        ModelRetry: Never raised for max_requests; 0 resolves to the configured default.
    """
    if max_requests < 1:
        max_requests = ctx.deps.config.subagent_max_requests_research

    policy = ctx.deps.config.web_policy
    if policy.search != "allow" or policy.fetch != "allow":
        raise ModelRetry(
            "Research sub-agent requires unrestricted web access; "
            "web_policy is not 'allow'. Handle this task directly."
        )

    from co_cli.tools._subagent_agents import make_research_agent

    registry = ctx.deps.services.model_registry
    if not registry or not registry.is_configured(ROLE_RESEARCH):
        raise ModelRetry("Research sub-agent is unavailable — handle this task directly.")
    rm = registry.get(ROLE_RESEARCH, ResolvedModel(model=ctx.model, settings=None))
    model_name = str(rm.model)
    role = ROLE_RESEARCH
    request_limit = max_requests
    agent = make_research_agent(rm)
    scoped_query = query if not domains else f"{query}\nRestrict searches to these domains: {', '.join(domains)}"
    with _TRACER.start_as_current_span(f"subagent_{role}") as span:
        span.set_attribute("subagent.role", role)
        span.set_attribute("subagent.model", model_name)
        span.set_attribute("subagent.request_limit", request_limit)
        attempt_1 = await _run_subagent_attempt(
            agent, scoped_query, ctx, max_requests, rm.settings,
            "Research sub-agent failed — handle this task directly.",
        )
        data = attempt_1.output

        # Empty-result retry: rephrased query when budget remains and result is empty
        remaining = max_requests - attempt_1.usage.requests
        if remaining > 0 and (not data.summary or not data.sources):
            retry_query = f"The previous search returned no results. Try with different keywords: {query} (alternative framing)."
            attempt_2 = await _run_subagent_attempt(
                agent, retry_query, ctx, remaining, rm.settings,
                "Research sub-agent retry failed — handle this task directly.",
            )
            data = attempt_2.output
            requests_used = attempt_1.usage.requests + attempt_2.usage.requests
        else:
            requests_used = attempt_1.usage.requests

        # Fallback: if result still empty (retry skipped or returned nothing), mark confidence=0.0
        if not data.summary or not data.sources:
            data = data.model_copy(update={"confidence": 0.0, "summary": data.summary or "No results found despite multiple searches."})

        span.set_attribute("subagent.requests_used", requests_used)
    scope = query[:ctx.deps.config.subagent_scope_chars]
    sources_text = "\n".join(f"- {s}" for s in data.sources) if data.sources else "No sources"
    display = f"Scope: {scope}\n{data.summary}\n\nSources:\n{sources_text}\n[{role} · {model_name} · {requests_used}/{request_limit} req]"
    return make_result(
        display,
        summary=data.summary,
        sources=data.sources,
        confidence=data.confidence,
        role=role,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=request_limit,
        scope=scope,
        run_id=attempt_1.run_id,
    )


async def run_analysis_subagent(
    ctx: RunContext[CoDeps],
    question: str,
    inputs: list[str] | None = None,
    max_requests: int = 0,
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
        max_requests: Maximum LLM requests the sub-agent may make (0 = use config default).

    Raises:
        ModelRetry: Never raised for max_requests; 0 resolves to the configured default.
    """
    if max_requests < 1:
        max_requests = ctx.deps.config.subagent_max_requests_analysis

    # No web_policy gate here: analysis sub-agent uses search_knowledge and
    # search_drive_files only — no web tools. If Drive ever gets a policy
    # setting, the gate belongs here, mirroring run_research_subagent above.

    from co_cli.tools._subagent_agents import make_analysis_agent

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
    with _TRACER.start_as_current_span(f"subagent_{role}") as span:
        span.set_attribute("subagent.role", role)
        span.set_attribute("subagent.model", model_name)
        span.set_attribute("subagent.request_limit", request_limit)
        attempt = await _run_subagent_attempt(
            agent, scoped_question, ctx, max_requests, rm.settings,
            "Analysis sub-agent failed — handle this task directly.",
        )
        requests_used = attempt.usage.requests
        span.set_attribute("subagent.requests_used", requests_used)
        data = attempt.output
    scope = question[:ctx.deps.config.subagent_scope_chars]
    evidence_text = "\n".join(f"- {e}" for e in data.evidence) if data.evidence else "No evidence"
    display = f"Scope: {scope}\n{data.conclusion}\n\nEvidence:\n{evidence_text}\n[{role} · {model_name} · {requests_used}/{request_limit} req]"
    return make_result(
        display,
        conclusion=data.conclusion,
        evidence=data.evidence,
        reasoning=data.reasoning,
        role=role,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=request_limit,
        scope=scope,
        run_id=attempt.run_id,
    )


async def run_thinking_subagent(
    ctx: RunContext[CoDeps],
    problem: str,
    max_requests: int = 0,
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
        max_requests: Maximum LLM requests the sub-agent may make (0 = use config default).

    Raises:
        ModelRetry: Never raised for max_requests; 0 resolves to the configured default.
    """
    if max_requests < 1:
        max_requests = ctx.deps.config.subagent_max_requests_thinking

    from co_cli.tools._subagent_agents import make_thinking_agent

    registry = ctx.deps.services.model_registry
    if not registry or not registry.is_configured(ROLE_REASONING):
        raise ModelRetry("Thinking sub-agent is unavailable — handle this task directly.")
    rm = registry.get(ROLE_REASONING, ResolvedModel(model=ctx.model, settings=None))
    model_name = str(rm.model)
    role = ROLE_REASONING
    request_limit = max_requests
    agent = make_thinking_agent(rm)
    with _TRACER.start_as_current_span(f"subagent_{role}") as span:
        span.set_attribute("subagent.role", role)
        span.set_attribute("subagent.model", model_name)
        span.set_attribute("subagent.request_limit", request_limit)
        attempt = await _run_subagent_attempt(
            agent, problem, ctx, max_requests, rm.settings,
            "Thinking sub-agent failed — handle this task directly.",
        )
        requests_used = attempt.usage.requests
        span.set_attribute("subagent.requests_used", requests_used)
        data = attempt.output
    scope = problem[:ctx.deps.config.subagent_scope_chars]
    steps_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(data.steps))
    display = f"Scope: {scope}\n{data.plan}\n\nSteps:\n{steps_text}\n\nConclusion:\n{data.conclusion}\n[{role} · {model_name} · {requests_used}/{request_limit} req]"
    return make_result(
        display,
        plan=data.plan,
        steps=data.steps,
        conclusion=data.conclusion,
        role=role,
        model_name=model_name,
        requests_used=requests_used,
        request_limit=request_limit,
        scope=scope,
        run_id=attempt.run_id,
    )
