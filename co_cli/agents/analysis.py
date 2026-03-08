"""Read-only analysis sub-agent for knowledge-base and Drive synthesis tasks."""

from pydantic import BaseModel
from pydantic_ai import Agent

from co_cli.agents._factory import make_subagent_model
from co_cli.config import ModelEntry
from co_cli.deps import CoDeps
from co_cli.tools.articles import search_knowledge
from co_cli.tools.google_drive import search_drive_files


class AnalysisResult(BaseModel):
    """Structured output from the analysis sub-agent."""

    conclusion: str
    evidence: list[str]
    reasoning: str


def make_analysis_agent(
    model_entry: ModelEntry,
    provider: str,
    ollama_host: str,
) -> Agent[CoDeps, AnalysisResult]:
    """Create a read-only analysis sub-agent with knowledge and Drive search tools.

    The agent receives an isolated CoDeps (via make_subagent_deps) and only
    has access to search_knowledge and search_drive_files — no write tools,
    no shell, no network. Use this for synthesis, comparison, and evaluation
    tasks against internal knowledge.
    """
    model = make_subagent_model(model_entry, provider, ollama_host)
    agent: Agent[CoDeps, AnalysisResult] = Agent(
        model,
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
