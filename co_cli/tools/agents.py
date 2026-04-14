"""Delegation tools — focused task agents with inline agent configuration."""

from copy import copy
from typing import Any

from opentelemetry import trace as otel_trace
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.deps import CoDeps, fork_deps
from co_cli.tools._agent_outputs import (
    AnalysisOutput,
    CodingOutput,
    ReasoningOutput,
    ResearchOutput,
)
from co_cli.tools.tool_output import tool_output

_TRACER = otel_trace.get_tracer("co-cli.agents")

# Maximum delegation depth — safety rail against accidental recursive delegation.
MAX_AGENT_DEPTH: int = 2


def _get_task_settings(role_key: str, deps: CoDeps) -> ModelSettings | None:
    """Return the ModelSettings for a delegation agent role.

    Reasoning delegation uses the main model's base settings (quirks-derived).
    All other delegation agents use NOREASON_SETTINGS.
    """
    if role_key == "reasoning":
        return deps.model.settings if deps.model else None
    return NOREASON_SETTINGS


def _merge_turn_usage(ctx: RunContext[CoDeps], usage: RunUsage) -> None:
    """Merge delegation agent usage into the parent turn's authoritative usage accumulator."""
    if ctx.deps.runtime.turn_usage is None:
        ctx.deps.runtime.turn_usage = usage
    else:
        ctx.deps.runtime.turn_usage.incr(usage)


def _format_output(
    role_key: str,
    data: Any,
    scope: str,
    model_name: str,
    requests_used: int,
    request_limit: int,
) -> tuple[str, dict[str, Any]]:
    """Format delegation agent output for display and extract metadata kwargs per role."""
    footer = f"[{role_key} · {model_name} · {requests_used}/{request_limit} req]"
    match role_key:
        case "coder":
            display = f"Scope: {scope}\nCoder analysis complete.\n{data.summary}\n{footer}"
            meta = dict(
                summary=data.summary,
                diff_preview=data.diff_preview,
                files_touched=data.files_touched,
                confidence=data.confidence,
            )
        case "researcher":
            src = "\n".join(f"- {s}" for s in data.sources) if data.sources else "No sources"
            display = f"Scope: {scope}\n{data.summary}\n\nSources:\n{src}\n{footer}"
            meta = dict(summary=data.summary, sources=data.sources, confidence=data.confidence)
        case "analyst":
            ev = "\n".join(f"- {e}" for e in data.evidence) if data.evidence else "No evidence"
            display = f"Scope: {scope}\n{data.conclusion}\n\nEvidence:\n{ev}\n{footer}"
            meta = dict(
                conclusion=data.conclusion, evidence=data.evidence, reasoning=data.reasoning
            )
        case "reasoner":
            steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(data.steps))
            display = f"Scope: {scope}\n{data.plan}\n\nSteps:\n{steps}\n\nConclusion:\n{data.conclusion}\n{footer}"
            meta = dict(plan=data.plan, steps=data.steps, conclusion=data.conclusion)
        case _:
            raise ValueError(f"Unknown role_key: {role_key!r}")
    return display, meta


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


# --- Instruction builders ---


def _coder_instructions(deps: CoDeps) -> str:
    return (
        f"You are a read-only code analysis agent. "
        f"Your file boundary is: {deps.workspace_root}. "
        f"Investigate the codebase using the available file tools and return a structured analysis. "
        f"You cannot write or modify files. Focus on understanding the code as-is."
    )


def _researcher_instructions(deps: CoDeps) -> str:
    return (
        "You are a read-only research agent. "
        "Search the web and fetch pages to answer the query. "
        "Synthesize what you find into a grounded summary with sources. "
        "Return a ResearchOutput with summary, sources (URLs), and confidence (0.0–1.0). "
        "Set confidence=0.0 only if you found nothing after exhausting available searches."
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
        f"Return a structured AnalysisOutput with a clear conclusion, "
        f"supporting evidence list, and your reasoning."
    )


def _reasoner_instructions(deps: CoDeps) -> str:
    return (
        "You are a reasoning agent. "
        "Decompose the problem, reason step-by-step, and return a structured result. "
        "Return a ReasoningOutput with: "
        "plan (1–3 sentence high-level approach), "
        "steps (ordered action steps), "
        "and conclusion (synthesized answer or recommendation)."
    )


# --- Delegation tools ---


async def delegate_coder(
    ctx: RunContext[CoDeps],
    task: str,
    max_requests: int = 0,
) -> ToolReturn:
    """Delegate codebase analysis to a read-only coder agent with file tools.

    When to use: multi-file code investigation, architecture tracing, impact
    analysis, or any task that requires reading and cross-referencing several
    files to produce a conclusion. Give a complete, concrete task description
    so the agent can work autonomously.

    When NOT to use: reading a single file, listing a directory, or grepping
    for a pattern — use the primitive file tools directly instead.

    Returns a CodingOutput with summary, diff_preview, files_touched, and
    confidence (0.0-1.0). Trust the result unless the confidence is low.

    Args:
        task: The analysis task to investigate.
        max_requests: Max LLM requests (0 = config default).
    """
    if ctx.deps.runtime.agent_depth >= MAX_AGENT_DEPTH:
        raise ModelRetry(
            f"Delegation depth limit reached ({MAX_AGENT_DEPTH}). Handle this task directly."
        )
    if not ctx.deps.model:
        raise ModelRetry("Coder agent is unavailable — handle this task directly.")

    from co_cli.agent._core import build_agent
    from co_cli.tools.files import find_in_files, list_directory, read_file

    budget = max_requests or ctx.deps.config.subagent.max_requests_coder
    model_obj = ctx.deps.model.model
    model_name = str(model_obj)
    task_settings = NOREASON_SETTINGS

    agent = build_agent(
        config=ctx.deps.config,
        model=model_obj,
        instructions=_coder_instructions(ctx.deps),
        tool_fns=[list_directory, read_file, find_in_files],
        output_type=CodingOutput,
    )

    child_deps = fork_deps(ctx.deps)
    child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback

    with _TRACER.start_as_current_span("delegate_coder") as span:
        span.set_attribute("agent.role", "coder")
        span.set_attribute("agent.model", model_name)
        span.set_attribute("agent.request_limit", budget)
        output, usage, run_id = await _run_agent_attempt(
            agent,
            task,
            ctx,
            budget,
            task_settings,
            "Coding agent failed — handle this task directly.",
            child_deps,
        )
        span.set_attribute("agent.requests_used", usage.requests)

    scope = task[: ctx.deps.config.subagent.scope_chars]
    display, extra_meta = _format_output(
        "coder", output, scope, model_name, usage.requests, budget
    )
    return tool_output(
        display,
        ctx=ctx,
        **extra_meta,
        role="coder",
        model_name=model_name,
        requests_used=usage.requests,
        request_limit=budget,
        scope=scope,
        run_id=run_id,
    )


async def delegate_researcher(
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
    from memory or the knowledge base — use web_fetch or search_knowledge
    directly instead.

    Returns a ResearchOutput with summary, sources (URLs), and confidence
    (0.0-1.0). Automatically retries once if the first search returns empty.

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

    from co_cli.agent._core import build_agent
    from co_cli.tools.web import web_fetch, web_search

    budget = max_requests or ctx.deps.config.subagent.max_requests_research
    model_obj = ctx.deps.model.model
    model_name = str(model_obj)
    task_settings = NOREASON_SETTINGS

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
        output_type=ResearchOutput,
    )

    child_deps = fork_deps(ctx.deps)
    child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback

    with _TRACER.start_as_current_span("delegate_researcher") as span:
        span.set_attribute("agent.role", "researcher")
        span.set_attribute("agent.model", model_name)
        span.set_attribute("agent.request_limit", budget)
        output, usage_1, run_id = await _run_agent_attempt(
            agent,
            scoped_prompt,
            ctx,
            budget,
            task_settings,
            "Research agent failed — handle this task directly.",
            child_deps,
        )
        requests_used = usage_1.requests

        # retry_on_empty: if first attempt returned nothing, retry once with rephrased query
        remaining = budget - usage_1.requests
        if remaining > 0 and (not output.summary or not output.sources):
            retry_query = (
                f"The previous search returned no results. "
                f"Try with different keywords: {query} (alternative framing)."
            )
            output_2, usage_2, _ = await _run_agent_attempt(
                agent,
                retry_query,
                ctx,
                remaining,
                task_settings,
                "Research agent retry failed — handle this task directly.",
                child_deps,
            )
            output = output_2
            requests_used = usage_1.requests + usage_2.requests
        if not output.summary or not output.sources:
            output = output.model_copy(
                update={
                    "confidence": 0.0,
                    "summary": output.summary or "No results found despite multiple searches.",
                }
            )

        span.set_attribute("agent.requests_used", requests_used)

    scope = query[: ctx.deps.config.subagent.scope_chars]
    display, extra_meta = _format_output(
        "researcher", output, scope, model_name, requests_used, budget
    )
    return tool_output(
        display,
        ctx=ctx,
        **extra_meta,
        role="researcher",
        model_name=model_name,
        requests_used=requests_used,
        request_limit=budget,
        scope=scope,
        run_id=run_id,
    )


async def delegate_analyst(
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
    use search_knowledge directly instead.

    Returns an AnalysisOutput with conclusion, evidence (list of supporting
    points), and reasoning (the chain of thought behind the conclusion).

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

    from co_cli.agent._core import build_agent
    from co_cli.tools.articles import search_knowledge
    from co_cli.tools.google_drive import search_drive_files

    budget = max_requests or ctx.deps.config.subagent.max_requests_analysis
    model_obj = ctx.deps.model.model
    model_name = str(model_obj)
    task_settings = NOREASON_SETTINGS

    scoped_prompt = question
    if inputs:
        scoped_prompt = "Context:\n" + "\n".join(inputs) + "\n\nQuestion: " + question

    agent = build_agent(
        config=ctx.deps.config,
        model=model_obj,
        instructions=_analyst_instructions(ctx.deps),
        tool_fns=[search_knowledge, search_drive_files],
        output_type=AnalysisOutput,
    )

    child_deps = fork_deps(ctx.deps)
    child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback

    with _TRACER.start_as_current_span("delegate_analyst") as span:
        span.set_attribute("agent.role", "analyst")
        span.set_attribute("agent.model", model_name)
        span.set_attribute("agent.request_limit", budget)
        output, usage, run_id = await _run_agent_attempt(
            agent,
            scoped_prompt,
            ctx,
            budget,
            task_settings,
            "Analysis agent failed — handle this task directly.",
            child_deps,
        )
        span.set_attribute("agent.requests_used", usage.requests)

    scope = question[: ctx.deps.config.subagent.scope_chars]
    display, extra_meta = _format_output(
        "analyst", output, scope, model_name, usage.requests, budget
    )
    return tool_output(
        display,
        ctx=ctx,
        **extra_meta,
        role="analyst",
        model_name=model_name,
        requests_used=usage.requests,
        request_limit=budget,
        scope=scope,
        run_id=run_id,
    )


async def delegate_reasoner(
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

    Returns a ReasoningOutput with plan (high-level approach), steps (ordered
    action items), and conclusion (synthesized answer or recommendation).

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

    from co_cli.agent._core import build_agent

    budget = max_requests or ctx.deps.config.subagent.max_requests_thinking
    model_obj = ctx.deps.model.model
    model_name = str(model_obj)
    # Reasoning agent uses the main model's base settings (quirks-derived, may include thinking tokens)
    task_settings = ctx.deps.model.settings

    agent = build_agent(
        config=ctx.deps.config,
        model=model_obj,
        instructions=_reasoner_instructions(ctx.deps),
        tool_fns=None,
        output_type=ReasoningOutput,
    )

    child_deps = fork_deps(ctx.deps)
    child_deps.runtime.tool_progress_callback = ctx.deps.runtime.tool_progress_callback

    with _TRACER.start_as_current_span("delegate_reasoner") as span:
        span.set_attribute("agent.role", "reasoner")
        span.set_attribute("agent.model", model_name)
        span.set_attribute("agent.request_limit", budget)
        output, usage, run_id = await _run_agent_attempt(
            agent,
            problem,
            ctx,
            budget,
            task_settings,
            "Reasoning agent failed — handle this task directly.",
            child_deps,
        )
        span.set_attribute("agent.requests_used", usage.requests)

    scope = problem[: ctx.deps.config.subagent.scope_chars]
    display, extra_meta = _format_output(
        "reasoner", output, scope, model_name, usage.requests, budget
    )
    return tool_output(
        display,
        ctx=ctx,
        **extra_meta,
        role="reasoner",
        model_name=model_name,
        requests_used=usage.requests,
        request_limit=budget,
        scope=scope,
        run_id=run_id,
    )
