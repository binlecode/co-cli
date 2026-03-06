"""Read-only research sub-agent for web search and synthesis tasks."""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from co_cli.agents._factory import make_subagent_model
from co_cli.deps import CoDeps
from co_cli.tools.web import web_fetch, web_search


class ResearchResult(BaseModel):
    """Structured output from the research sub-agent."""

    summary: str
    sources: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


def make_research_agent(
    model_name: str,
    provider: str,
    ollama_host: str,
) -> Agent[CoDeps, ResearchResult]:
    """Create a read-only research sub-agent with web tools.

    The agent receives an isolated CoDeps (via make_subagent_deps) and only
    has access to web_search and web_fetch — no write tools, no shell, no memory.
    """
    model = make_subagent_model(model_name, provider, ollama_host)
    agent: Agent[CoDeps, ResearchResult] = Agent(
        model,
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
