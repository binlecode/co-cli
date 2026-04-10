"""Sub-agent helpers — result types and agent factories for co_cli/tools/subagent.py."""

from typing import Any, cast

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from co_cli.deps import CoDeps
from co_cli.tools.articles import search_knowledge
from co_cli.tools.files import find_in_files, list_directory, read_file
from co_cli.tools.google_drive import search_drive_files
from co_cli.tools.web import web_fetch, web_search


def _make_base_agent(model: Any, output_type: type, instructions: str) -> Agent:
    return Agent(model, deps_type=CoDeps, output_type=output_type, instructions=instructions)


class CoderOutput(BaseModel):
    """Structured output from the coder sub-agent."""

    summary: str
    diff_preview: str
    files_touched: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


def make_coder_agent(model: Any) -> Agent[CoDeps, CoderOutput]:
    """Create a read-only coder sub-agent with file tools.

    The agent receives an isolated CoDeps (via make_subagent_deps in the
    subagent tool) and only has access to read-only file tools — no writes,
    no shell, no network.

    Caller passes model_settings at agent.run() time.
    """
    agent = cast(
        Agent[CoDeps, CoderOutput],
        _make_base_agent(
            model,
            CoderOutput,
            (
                "You are a read-only code analysis agent. "
                "Investigate the codebase using the available file tools and return a structured analysis. "
                "You cannot write or modify files. Focus on understanding the code as-is."
            ),
        ),
    )
    agent.tool(list_directory, requires_approval=False)  # type: ignore[arg-type]  # pydantic-ai tool() overloads require exact AgentDepsT match; cast above is correct
    agent.tool(read_file, requires_approval=False)  # type: ignore[arg-type]  # same as above
    agent.tool(find_in_files, requires_approval=False)  # type: ignore[arg-type]  # same as above
    return agent


class ResearchOutput(BaseModel):
    """Structured output from the research sub-agent."""

    summary: str
    sources: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


def make_research_agent(model: Any) -> Agent[CoDeps, ResearchOutput]:
    """Create a read-only research sub-agent with web tools.

    The agent receives an isolated CoDeps (via make_subagent_deps) and only
    has access to web_search and web_fetch — no write tools, no shell, no memory.

    Caller passes model_settings at agent.run() time.
    """
    agent = cast(
        Agent[CoDeps, ResearchOutput],
        _make_base_agent(
            model,
            ResearchOutput,
            (
                "You are a read-only research agent. "
                "Search the web and fetch pages to answer the query. "
                "Synthesize what you find into a grounded summary with sources. "
                "Return a ResearchOutput with summary, sources (URLs), and confidence (0.0–1.0). "
                "Set confidence=0.0 only if you found nothing after exhausting available searches."
            ),
        ),
    )
    agent.tool(web_search, requires_approval=False)  # type: ignore[arg-type]  # pydantic-ai tool() overloads require exact AgentDepsT match; cast above is correct
    agent.tool(web_fetch, requires_approval=False)  # type: ignore[arg-type]  # same as above
    return agent


class AnalysisOutput(BaseModel):
    """Structured output from the analysis sub-agent."""

    conclusion: str
    evidence: list[str]
    reasoning: str


def make_analysis_agent(model: Any) -> Agent[CoDeps, AnalysisOutput]:
    """Create a read-only analysis sub-agent with knowledge and Drive search tools.

    The agent receives an isolated CoDeps (via make_subagent_deps) and only
    has access to search_knowledge and search_drive_files — no write tools,
    no shell, no network. Use this for synthesis, comparison, and evaluation
    tasks against internal knowledge.

    Caller passes model_settings at agent.run() time.
    """
    agent = cast(
        Agent[CoDeps, AnalysisOutput],
        _make_base_agent(
            model,
            AnalysisOutput,
            (
                "You are a read-only analysis agent. "
                "Use the available search tools to gather evidence, then compare, evaluate, "
                "and synthesize the provided inputs. "
                "Return a structured AnalysisOutput with a clear conclusion, "
                "supporting evidence list, and your reasoning."
            ),
        ),
    )
    agent.tool(search_knowledge, requires_approval=False)  # type: ignore[arg-type]  # pydantic-ai tool() overloads require exact AgentDepsT match; cast above is correct
    agent.tool(search_drive_files, requires_approval=False)  # type: ignore[arg-type]  # same as above
    return agent


class ThinkingOutput(BaseModel):
    """Structured output from the thinking sub-agent."""

    plan: str
    steps: list[str]
    conclusion: str


def make_thinking_agent(model: Any) -> Agent[CoDeps, ThinkingOutput]:
    """Create a reasoning-only thinking sub-agent with no tools.

    The agent receives an isolated CoDeps (via make_subagent_deps) and has
    NO registered tools — it reasons purely via the model's native thinking
    capability (extended thinking tokens, chain-of-thought). Use this for
    structured problem decomposition, planning, and synthesis tasks that
    benefit from a dedicated reasoning pass.

    Caller passes model_settings at agent.run() time.
    """
    return cast(
        Agent[CoDeps, ThinkingOutput],
        _make_base_agent(
            model,
            ThinkingOutput,
            (
                "You are a reasoning agent. "
                "Decompose the problem, reason step-by-step, and return a structured result. "
                "Return a ThinkingOutput with: "
                "plan (1–3 sentence high-level approach), "
                "steps (ordered action steps), "
                "and conclusion (synthesized answer or recommendation)."
            ),
        ),
    )
