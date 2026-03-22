"""Sub-agent helpers — result types and agent factories for co_cli/tools/subagent.py."""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from co_cli._model_factory import ResolvedModel
from co_cli.deps import CoDeps
from co_cli.tools.articles import search_knowledge
from co_cli.tools.files import find_in_files, list_directory, read_file
from co_cli.tools.google_drive import search_drive_files
from co_cli.tools.web import web_fetch, web_search


class CoderResult(BaseModel):
    """Structured output from the coder sub-agent."""

    summary: str
    diff_preview: str
    files_touched: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


def make_coder_agent(resolved_model: ResolvedModel) -> Agent[CoDeps, CoderResult]:
    """Create a read-only coder sub-agent with file tools.

    The agent receives an isolated CoDeps (via make_subagent_deps in the
    subagent tool) and only has access to read-only file tools — no writes,
    no shell, no network.

    Caller passes model_settings=resolved_model.settings to agent.run().
    """
    agent: Agent[CoDeps, CoderResult] = Agent(
        resolved_model.model,
        deps_type=CoDeps,
        output_type=CoderResult,
        system_prompt=(
            "You are a read-only code analysis agent. "
            "Investigate the codebase using the available file tools and return a structured analysis. "
            "You cannot write or modify files. Focus on understanding the code as-is."
        ),
    )
    agent.tool(list_directory, requires_approval=False)
    agent.tool(read_file, requires_approval=False)
    agent.tool(find_in_files, requires_approval=False)
    return agent


class ResearchResult(BaseModel):
    """Structured output from the research sub-agent."""

    summary: str
    sources: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


def make_research_agent(resolved_model: ResolvedModel) -> Agent[CoDeps, ResearchResult]:
    """Create a read-only research sub-agent with web tools.

    The agent receives an isolated CoDeps (via make_subagent_deps) and only
    has access to web_search and web_fetch — no write tools, no shell, no memory.

    Caller passes model_settings=resolved_model.settings to agent.run().
    """
    agent: Agent[CoDeps, ResearchResult] = Agent(
        resolved_model.model,
        deps_type=CoDeps,
        output_type=ResearchResult,
        system_prompt=(
            "You are a read-only research agent. "
            "Search the web and fetch pages to answer the query. "
            "Synthesize what you find into a grounded summary with sources. "
            "Return a ResearchResult with summary, sources (URLs), and confidence (0.0–1.0). "
            "Set confidence=0.0 only if you found nothing after exhausting available searches."
        ),
    )
    agent.tool(web_search, requires_approval=False)
    agent.tool(web_fetch, requires_approval=False)
    return agent


class AnalysisResult(BaseModel):
    """Structured output from the analysis sub-agent."""

    conclusion: str
    evidence: list[str]
    reasoning: str


def make_analysis_agent(resolved_model: ResolvedModel) -> Agent[CoDeps, AnalysisResult]:
    """Create a read-only analysis sub-agent with knowledge and Drive search tools.

    The agent receives an isolated CoDeps (via make_subagent_deps) and only
    has access to search_knowledge and search_drive_files — no write tools,
    no shell, no network. Use this for synthesis, comparison, and evaluation
    tasks against internal knowledge.

    Caller passes model_settings=resolved_model.settings to agent.run().
    """
    agent: Agent[CoDeps, AnalysisResult] = Agent(
        resolved_model.model,
        deps_type=CoDeps,
        output_type=AnalysisResult,
        system_prompt=(
            "You are a read-only analysis agent. "
            "Use the available search tools to gather evidence, then compare, evaluate, "
            "and synthesize the provided inputs. "
            "Return a structured AnalysisResult with a clear conclusion, "
            "supporting evidence list, and your reasoning."
        ),
    )
    agent.tool(search_knowledge, requires_approval=False)
    agent.tool(search_drive_files, requires_approval=False)
    return agent


class ThinkingResult(BaseModel):
    """Structured output from the thinking sub-agent."""

    plan: str
    steps: list[str]
    conclusion: str


def make_thinking_agent(resolved_model: ResolvedModel) -> Agent[CoDeps, ThinkingResult]:
    """Create a reasoning-only thinking sub-agent with no tools.

    The agent receives an isolated CoDeps (via make_subagent_deps) and has
    NO registered tools — it reasons purely via the model's native thinking
    capability (extended thinking tokens, chain-of-thought). Use this for
    structured problem decomposition, planning, and synthesis tasks that
    benefit from a dedicated reasoning pass.

    Caller passes model_settings=resolved_model.settings to agent.run().
    """
    agent: Agent[CoDeps, ThinkingResult] = Agent(
        resolved_model.model,
        deps_type=CoDeps,
        output_type=ThinkingResult,
        system_prompt=(
            "You are a reasoning agent. "
            "Decompose the problem, reason step-by-step, and return a structured result. "
            "Return a ThinkingResult with: "
            "plan (1–3 sentence high-level approach), "
            "steps (ordered action steps), "
            "and conclusion (synthesized answer or recommendation)."
        ),
    )
    # No tools registered — pure native reasoning, no external calls.
    return agent
