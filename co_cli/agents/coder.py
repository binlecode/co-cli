"""Read-only coder sub-agent for code analysis and investigation tasks."""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from co_cli.agents._factory import ResolvedModel
from co_cli.deps import CoDeps
from co_cli.tools.files import find_in_files, list_directory, read_file


class CoderResult(BaseModel):
    """Structured output from the coder sub-agent."""

    summary: str
    diff_preview: str
    files_touched: list[str]
    confidence: float = Field(ge=0.0, le=1.0)


def make_coder_agent(resolved_model: ResolvedModel) -> Agent[CoDeps, CoderResult]:
    """Create a read-only coder sub-agent with file tools.

    The agent receives an isolated CoDeps (via make_subagent_deps in the
    delegation tool) and only has access to read-only file tools — no writes,
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
