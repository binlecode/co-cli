from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.tools.google_drive import search_drive, read_drive_file
from co_cli.tools.google_gmail import list_emails, search_emails, draft_email
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.slack import post_slack_message


def get_agent() -> Agent[CoDeps, str]:
    """Factory function to create the Pydantic AI Agent.

    Supports 'ollama' and 'gemini' (default) via config.
    """
    provider_name = settings.llm_provider.lower()

    if provider_name == "gemini":
        api_key = settings.gemini_api_key
        model_name = settings.gemini_model
        if not api_key:
            raise ValueError("gemini_api_key is required in settings when llm_provider is 'gemini'.")

        # Use model string format - pydantic-ai will use GEMINI_API_KEY env var
        # Format: google-gla:<model_name>
        import os
        os.environ.setdefault("GEMINI_API_KEY", api_key)
        model = f"google-gla:{model_name}"
    else:
        # Default to Ollama
        ollama_host = settings.ollama_host
        model_name = settings.ollama_model

        # Ollama's OpenAI-compatible API is at /v1
        base_url = f"{ollama_host}/v1"

        provider = OpenAIProvider(base_url=base_url, api_key="ollama")
        model = OpenAIChatModel(
            model_name=model_name,
            provider=provider
        )

    system_prompt = """You are Co, a CLI assistant running in the user's terminal.

### Response Style
- Show tool output directly—don't summarize or paraphrase
- Be terse: users want results, not explanations
- On success: show the output, then a brief note if needed
- On error: show the error, suggest a fix

### Tool Usage
- Use tools proactively to complete tasks
- Chain operations: read before modifying, test after changing
- Shell commands run in a Docker sandbox mounted at /workspace
"""

    agent: Agent[CoDeps, str] = Agent(
        model,
        deps_type=CoDeps,
        system_prompt=system_prompt,
    )

    # Register tools with RunContext pattern
    agent.tool(run_shell_command)
    agent.tool(search_notes)
    agent.tool(list_notes)
    agent.tool(read_note)

    # Batch 3-4: Google + Slack tools (migrated to RunContext)
    agent.tool(search_drive)
    agent.tool(read_drive_file)
    agent.tool(list_emails)
    agent.tool(search_emails)
    agent.tool(draft_email)
    agent.tool(list_calendar_events)
    agent.tool(search_calendar_events)
    agent.tool(post_slack_message)

    return agent
