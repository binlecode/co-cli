from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from co_cli.config import settings, WebPolicy
from co_cli.deps import CoDeps
from co_cli._history import truncate_tool_returns, truncate_history_window
from co_cli.prompts import get_system_prompt
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.tools.google_drive import search_drive_files, read_drive_file
from co_cli.tools.google_gmail import list_emails, search_emails, create_email_draft
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.slack import (
    send_slack_message,
    list_slack_channels,
    list_slack_messages,
    list_slack_replies,
    list_slack_users,
)
from co_cli.tools.web import web_search, web_fetch


def get_agent(
    *,
    all_approval: bool = False,
    web_policy: WebPolicy | None = None,
) -> tuple[Agent[CoDeps, str | DeferredToolRequests], ModelSettings | None, list[str]]:
    """Factory function to create the Pydantic AI Agent.

    Supports 'ollama' and 'gemini' (default) via config.

    Args:
        all_approval: When True, register ALL tools with requires_approval=True.
            Used by the eval framework so every tool call returns
            DeferredToolRequests without executing (no ModelRetry loops).
        web_policy: Per-tool web permission policy. "ask" is wired here via
            requires_approval while "deny" is enforced inside tool execution.
    """
    provider_name = settings.llm_provider.lower()

    model_settings: ModelSettings | None = None

    if provider_name == "gemini":
        api_key = settings.gemini_api_key
        model_name = settings.gemini_model
        if not api_key:
            raise ValueError("gemini_api_key is required in settings when llm_provider is 'gemini'.")

        # pydantic-ai reads GEMINI_API_KEY from the environment.
        # Direct assignment so the settings value always wins over a stale env var.
        import os
        os.environ["GEMINI_API_KEY"] = api_key
        model = f"google-gla:{model_name}"
    elif provider_name == "ollama":
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
    else:
        raise ValueError(
            f"Unknown llm_provider: '{provider_name}'. Use 'gemini' or 'ollama'."
        )

    system_prompt = get_system_prompt(provider_name, personality=settings.personality)

    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        model,
        deps_type=CoDeps,
        system_prompt=system_prompt,
        retries=settings.tool_retries,
        output_type=[str, DeferredToolRequests],
        history_processors=[truncate_tool_returns, truncate_history_window],
    )

    # Side-effectful tools — require human approval via DeferredToolRequests
    agent.tool(run_shell_command, requires_approval=True)
    agent.tool(create_email_draft, requires_approval=True)
    agent.tool(send_slack_message, requires_approval=True)

    # Read-only tools — no approval needed (unless all_approval for eval)
    agent.tool(search_notes, requires_approval=all_approval)
    agent.tool(list_notes, requires_approval=all_approval)
    agent.tool(read_note, requires_approval=all_approval)
    agent.tool(search_drive_files, requires_approval=all_approval)
    agent.tool(read_drive_file, requires_approval=all_approval)
    agent.tool(list_emails, requires_approval=all_approval)
    agent.tool(search_emails, requires_approval=all_approval)
    agent.tool(list_calendar_events, requires_approval=all_approval)
    agent.tool(search_calendar_events, requires_approval=all_approval)
    agent.tool(list_slack_channels, requires_approval=all_approval)
    agent.tool(list_slack_messages, requires_approval=all_approval)
    agent.tool(list_slack_replies, requires_approval=all_approval)
    agent.tool(list_slack_users, requires_approval=all_approval)
    policy = web_policy or settings.web_policy
    search_approval = all_approval or (policy.search == "ask")
    fetch_approval = all_approval or (policy.fetch == "ask")
    agent.tool(web_search, requires_approval=search_approval)
    agent.tool(web_fetch, requires_approval=fetch_approval)

    tool_names = [
        run_shell_command.__name__, create_email_draft.__name__, send_slack_message.__name__,
        search_notes.__name__, list_notes.__name__, read_note.__name__,
        search_drive_files.__name__, read_drive_file.__name__,
        list_emails.__name__, search_emails.__name__,
        list_calendar_events.__name__, search_calendar_events.__name__,
        list_slack_channels.__name__, list_slack_messages.__name__,
        list_slack_replies.__name__, list_slack_users.__name__,
        web_search.__name__, web_fetch.__name__,
    ]

    return agent, model_settings, tool_names
