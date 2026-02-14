import os

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from co_cli.config import settings, WebPolicy, MCPServerConfig
from co_cli.deps import CoDeps
from co_cli._history import truncate_tool_returns, truncate_history_window
from co_cli.prompts import assemble_prompt
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.tools.google_drive import search_drive_files, read_drive_file
from co_cli.tools.google_gmail import list_emails, search_emails, create_email_draft
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.web import web_search, web_fetch
from co_cli.tools.memory import save_memory, recall_memory, list_memories


def get_agent(
    *,
    all_approval: bool = False,
    web_policy: WebPolicy | None = None,
    mcp_servers: dict[str, MCPServerConfig] | None = None,
) -> tuple[Agent[CoDeps, str | DeferredToolRequests], ModelSettings | None, list[str]]:
    """Factory function to create the Pydantic AI Agent.

    Supports 'ollama' and 'gemini' (default) via config.

    Args:
        all_approval: When True, register ALL tools with requires_approval=True.
            Used by the eval framework so every tool call returns
            DeferredToolRequests without executing (no ModelRetry loops).
        web_policy: Per-tool web permission policy. "ask" is wired here via
            requires_approval while "deny" is enforced inside tool execution.
        mcp_servers: MCP server configs to connect as toolsets. Servers are
            started/stopped via ``async with agent``.
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

        # Model-specific inference parameters from quirk database
        from co_cli.prompts.model_quirks import get_model_inference, normalize_model_name as _normalize
        inf = get_model_inference("ollama", _normalize(model_name))
        num_ctx = inf.get("num_ctx", settings.ollama_num_ctx)
        extra: dict = {"num_ctx": num_ctx}
        extra.update(inf.get("extra_body", {}))

        model_settings = ModelSettings(
            temperature=inf.get("temperature", 0.7),
            top_p=inf.get("top_p", 1.0),
            max_tokens=inf.get("max_tokens", 16384),
            extra_body=extra,
        )
    else:
        raise ValueError(
            f"Unknown llm_provider: '{provider_name}'. Use 'gemini' or 'ollama'."
        )

    # Normalize model name for quirk lookup (strips quantization tags like ":q4_k_m")
    from co_cli.prompts.model_quirks import normalize_model_name
    normalized_model = normalize_model_name(model_name)
    system_prompt, _manifest = assemble_prompt(
        provider_name,
        model_name=normalized_model,
        personality=settings.personality,
    )

    # Build MCP toolsets from config
    mcp_toolsets = []
    if mcp_servers:
        from pydantic_ai.mcp import MCPServerStdio

        for name, cfg in mcp_servers.items():
            env = dict(cfg.env) if cfg.env else {}
            # Lazy GitHub token: resolve at session start, not at config import
            if name == "github" and "GITHUB_PERSONAL_ACCESS_TOKEN" not in env:
                token = os.getenv("GITHUB_TOKEN_BINLECODE", "")
                if token:
                    env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
            server = MCPServerStdio(
                cfg.command,
                args=cfg.args,
                timeout=cfg.timeout,
                env=env or None,
                tool_prefix=cfg.prefix or name,
            )
            if cfg.approval == "auto":
                server = server.approval_required()
            mcp_toolsets.append(server)

    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        model,
        deps_type=CoDeps,
        system_prompt=system_prompt,
        retries=settings.tool_retries,
        output_type=[str, DeferredToolRequests],
        history_processors=[truncate_tool_returns, truncate_history_window],
        toolsets=mcp_toolsets if mcp_toolsets else None,
    )

    # Side-effectful tools — require human approval via DeferredToolRequests
    agent.tool(run_shell_command, requires_approval=True)
    agent.tool(create_email_draft, requires_approval=True)
    agent.tool(save_memory, requires_approval=True)

    # Read-only tools — no approval needed (unless all_approval for eval)
    agent.tool(recall_memory, requires_approval=all_approval)
    agent.tool(list_memories, requires_approval=all_approval)
    agent.tool(search_notes, requires_approval=all_approval)
    agent.tool(list_notes, requires_approval=all_approval)
    agent.tool(read_note, requires_approval=all_approval)
    agent.tool(search_drive_files, requires_approval=all_approval)
    agent.tool(read_drive_file, requires_approval=all_approval)
    agent.tool(list_emails, requires_approval=all_approval)
    agent.tool(search_emails, requires_approval=all_approval)
    agent.tool(list_calendar_events, requires_approval=all_approval)
    agent.tool(search_calendar_events, requires_approval=all_approval)
    policy = web_policy or settings.web_policy
    search_approval = all_approval or (policy.search == "ask")
    fetch_approval = all_approval or (policy.fetch == "ask")
    agent.tool(web_search, requires_approval=search_approval)
    agent.tool(web_fetch, requires_approval=fetch_approval)

    tool_names = list(agent._function_toolset.tools.keys())
    return agent, model_settings, tool_names
