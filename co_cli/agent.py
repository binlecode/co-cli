from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from co_cli.config import settings
from co_cli.deps import CoDeps
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.tools.google_drive import search_drive, read_drive_file
from co_cli.tools.google_gmail import list_emails, search_emails, draft_email
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.slack import post_slack_message


def get_agent() -> tuple[Agent[CoDeps, str], ModelSettings | None]:
    """Factory function to create the Pydantic AI Agent.

    Supports 'ollama' and 'gemini' (default) via config.
    """
    provider_name = settings.llm_provider.lower()

    model_settings: ModelSettings | None = None

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

        # GLM-4.7-Flash "Terminal / SWE-Bench Verified" profile.
        # Best match for Co CLI's tool-calling pattern (shell commands + API calls).
        # See: https://huggingface.co/zai-org/GLM-4.7-Flash
        model_settings = ModelSettings(
            temperature=0.7,
            top_p=1.0,
            max_tokens=16384,
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

### Pagination
- When a tool result has has_more=true, more results are available
- If the user asks for "more", "next", or "next 10", call the same tool with the same query and page incremented by 1
- Do NOT say "no more results" unless you called the tool and has_more was false
"""

    agent: Agent[CoDeps, str] = Agent(
        model,
        deps_type=CoDeps,
        system_prompt=system_prompt,
        retries=settings.tool_retries,
    )

    # All tools inherit agent-level retries (settings.tool_retries).
    # Side-effectful tools (shell, email, slack) are gated by confirm_or_yolo,
    # so retries are safe — the user confirms each attempt.
    agent.tool(run_shell_command)
    agent.tool(search_notes)
    agent.tool(list_notes)
    agent.tool(read_note)
    agent.tool(search_drive)
    agent.tool(read_drive_file)
    agent.tool(list_emails)
    agent.tool(search_emails)
    agent.tool(draft_email)
    agent.tool(list_calendar_events)
    agent.tool(search_calendar_events)
    agent.tool(post_slack_message)

    return agent, model_settings
