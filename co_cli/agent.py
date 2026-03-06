import os
from datetime import date
from pathlib import Path

import httpx

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from co_cli.config import settings, WebPolicy, MCPServerConfig
from co_cli.deps import CoDeps
from co_cli._history import (
    inject_opening_context,
    truncate_tool_returns,
    detect_safety_issues,
    truncate_history_window,
)
from co_cli.prompts import assemble_prompt
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import search_notes, list_notes, read_note
from co_cli.tools.google_drive import search_drive_files, read_drive_file
from co_cli.tools.google_gmail import list_emails, search_emails, create_email_draft
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.web import web_search, web_fetch
from co_cli.tools.memory import save_memory, recall_memory, list_memories, update_memory, append_memory
from co_cli.tools.articles import save_article, recall_article, read_article_detail, search_knowledge
from co_cli.tools.todo import todo_write, todo_read
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.files import list_directory, read_file, find_in_files, write_file, edit_file
from co_cli.tools.delegation import delegate_coder, delegate_research, delegate_analysis
from co_cli.tools.task_control import (
    start_background_task,
    check_task_status,
    cancel_background_task,
    list_background_tasks,
)


def get_agent(
    *,
    all_approval: bool = False,
    web_policy: WebPolicy | None = None,
    mcp_servers: dict[str, MCPServerConfig] | None = None,
    personality: str | None = None,
    model_name: str | None = None,
) -> tuple[Agent[CoDeps, str | DeferredToolRequests], ModelSettings | None, list[str], dict[str, bool]]:
    """Factory function to create the Pydantic AI Agent.

    Supports 'ollama' (default) and 'gemini' via config.

    Args:
        all_approval: When True, register ALL tools with requires_approval=True.
            Used by the eval framework so every tool call returns
            DeferredToolRequests without executing (no ModelRetry loops).
        web_policy: Per-tool web permission policy. "ask" is wired here via
            requires_approval while "deny" is enforced inside tool execution.
        mcp_servers: MCP server configs to connect as toolsets. Servers are
            started/stopped via ``async with agent``.
        personality: Active soul name (e.g., "finch", "jeff"). The soul seed
            (identity declaration) is extracted and placed at the top of the
            static system prompt — the model's first context is always the soul.
    """
    provider_name = settings.llm_provider.lower()

    model_settings: ModelSettings | None = None

    active_model_name = model_name or settings.model_roles["reasoning"][0]

    if provider_name == "gemini":
        api_key = settings.gemini_api_key
        if not api_key:
            raise ValueError("gemini_api_key is required in settings when llm_provider is 'gemini'.")

        # pydantic-ai reads GEMINI_API_KEY from the environment.
        # Direct assignment so the settings value always wins over a stale env var.
        os.environ["GEMINI_API_KEY"] = api_key
        model = f"google-gla:{active_model_name}"

        # Model-specific inference parameters from quirk database.
        # Fallback defaults keep Gemini on a thinking-safe profile even when
        # no model-specific quirk file exists.
        from co_cli.prompts.model_quirks import get_model_inference, normalize_model_name as _normalize
        inf = get_model_inference("gemini", _normalize(active_model_name))
        model_settings = ModelSettings(
            temperature=inf.get("temperature", 1.0),
            top_p=inf.get("top_p", 0.95),
            max_tokens=inf.get("max_tokens", 65536),
        )
    elif provider_name == "ollama":
        ollama_host = settings.ollama_host

        # Ollama's OpenAI-compatible API is at /v1
        base_url = f"{ollama_host}/v1"

        # Explicit timeout: default openai-sdk read timeout is 600s, but large local
        # models can exceed that on slow hardware. 300s covers realistic local inference
        # while failing fast enough to surface real problems.
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
        )
        provider = OpenAIProvider(base_url=base_url, api_key="ollama", http_client=_http_client)
        model = OpenAIChatModel(
            model_name=active_model_name,
            provider=provider
        )

        # Model-specific inference parameters from quirk database
        from co_cli.prompts.model_quirks import get_model_inference, normalize_model_name as _normalize
        inf = get_model_inference("ollama", _normalize(active_model_name))
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
    normalized_model = normalize_model_name(active_model_name)

    # Soul block — seed + character base memories first; examples trail the rules
    soul_seed: str | None = None
    soul_examples: str | None = None
    if personality:
        from co_cli.prompts.personalities._composer import (
            load_soul_seed,
            load_soul_examples,
            load_soul_mindsets,
            load_character_memories,
        )
        soul_seed = load_soul_seed(personality)
        memory_dir = Path.cwd() / ".co-cli" / "knowledge"
        base_memories = load_character_memories(personality, memory_dir)
        if base_memories:
            soul_seed = soul_seed + "\n\n" + base_memories
        soul_mindsets = load_soul_mindsets(personality)
        if soul_mindsets:
            soul_seed = soul_seed + "\n\n" + soul_mindsets
        examples = load_soul_examples(personality)
        if examples:
            soul_examples = examples

    system_prompt, _manifest = assemble_prompt(
        provider_name,
        model_name=normalized_model,
        soul_seed=soul_seed,
        soul_examples=soul_examples,
    )

    # Build MCP toolsets from config
    mcp_toolsets = []
    if mcp_servers:
        from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP, MCPServerSSE

        for name, cfg in mcp_servers.items():
            if cfg.url:
                # HTTP transport — SSE when URL ends with /sse, else StreamableHTTP
                if cfg.url.rstrip("/").endswith("/sse"):
                    server = MCPServerSSE(cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout)
                else:
                    server = MCPServerStreamableHTTP(cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout)
            else:
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
        history_processors=[
            inject_opening_context,
            truncate_tool_returns,
            detect_safety_issues,
            truncate_history_window,
        ],
        toolsets=mcp_toolsets if mcp_toolsets else None,
    )

    # Conditional prompt layers — runtime-gated via @agent.instructions (fresh per turn, never accumulated)

    @agent.instructions
    def add_current_date(ctx: RunContext[CoDeps]) -> str:
        """Inject the current date so the model can reason about time."""
        return f"Today is {date.today().isoformat()}."

    @agent.instructions
    def add_shell_guidance(ctx: RunContext[CoDeps]) -> str:
        """Inject shell tool guidance when shell is available."""
        return (
            "Shell runs as subprocess. DENY-pattern commands are blocked before deferral. "
            "Safe-prefix commands execute directly. All others require user approval."
        )

    @agent.instructions
    def add_project_instructions(ctx: RunContext[CoDeps]) -> str:
        """Inject project-level instructions from .co-cli/instructions.md."""
        instructions_path = Path.cwd() / ".co-cli" / "instructions.md"
        if instructions_path.is_file():
            return instructions_path.read_text(encoding="utf-8").strip()
        return ""

    @agent.instructions
    def add_personality_memories(ctx: RunContext[CoDeps]) -> str:
        """Inject personality-context memories for relationship continuity."""
        if not ctx.deps.personality:
            return ""
        from co_cli.tools.personality import _load_personality_memories
        return _load_personality_memories()

    @agent.instructions
    def inject_personality_critique(ctx: RunContext[CoDeps]) -> str:
        """Inject always-on soul critique from souls/{role}/critique.md."""
        if not ctx.deps.personality_critique:
            return ""
        return f"\n## Review lens\n\n{ctx.deps.personality_critique}"

    @agent.instructions
    def add_available_skills(ctx: RunContext[CoDeps]) -> str:
        """Inject the list of available skills so the model can route /skill commands."""
        if not ctx.deps.skill_registry:
            return ""
        lines = ["## Available Skills"]
        for entry in ctx.deps.skill_registry:
            lines.append(f"/{entry['name']} — {entry['description']}")
        text = "\n".join(lines)
        # Cap at 2KB; append truncation notice if needed
        if len(text) > 2048:
            # Count skills that fit within budget
            budget = 2048 - 40
            truncated = text[:budget]
            shown = truncated.count("\n")
            remaining = len(ctx.deps.skill_registry) - shown
            text = truncated + f"\n(+{remaining} more — type / to see all)"
        return text

    tool_registry: list[tuple[str, bool]] = []  # (name, requires_approval)

    def _register(fn, requires_approval: bool) -> None:
        agent.tool(fn, requires_approval=requires_approval)
        tool_registry.append((fn.__name__, requires_approval))

    # Background task management
    _register(start_background_task, True)
    _register(check_task_status, False)
    _register(cancel_background_task, False)
    _register(list_background_tasks, False)

    # Capability introspection — no approval (read-only, no side effects)
    _register(check_capabilities, False)

    # Sub-agent delegation — no approval (read-only tools only, gated by model_roles setting)
    _register(delegate_coder, False)
    _register(delegate_research, False)
    _register(delegate_analysis, False)

    # Native file tools
    _register(list_directory, False)
    _register(read_file, False)
    _register(find_in_files, False)
    _register(write_file, True)
    _register(edit_file, True)

    # Shell: policy (DENY/ALLOW/REQUIRE_APPROVAL) is evaluated inside the tool.
    # DENY and ALLOW are decided before any deferral; only REQUIRE_APPROVAL defers.
    _register(run_shell_command, False)
    _register(create_email_draft, True)
    _register(save_memory, True)
    _register(save_article, True)
    _register(update_memory, all_approval)
    _register(append_memory, all_approval)

    # Session task tracking — no approval (in-memory only, no external side effects)
    _register(todo_write, all_approval)
    _register(todo_read, all_approval)

    # Read-only tools — no approval needed (unless all_approval for eval)
    _register(list_memories, all_approval)
    _register(read_article_detail, all_approval)
    _register(search_knowledge, all_approval)
    _register(list_notes, all_approval)
    _register(read_note, all_approval)
    _register(search_drive_files, all_approval)
    _register(read_drive_file, all_approval)
    _register(list_emails, all_approval)
    _register(search_emails, all_approval)
    _register(list_calendar_events, all_approval)
    _register(search_calendar_events, all_approval)
    policy = web_policy or settings.web_policy
    search_approval = all_approval or (policy.search == "ask")
    fetch_approval = all_approval or (policy.fetch == "ask")
    _register(web_search, search_approval)
    _register(web_fetch, fetch_approval)

    tool_names = [name for name, _ in tool_registry]
    tool_approval = {name: flag for name, flag in tool_registry}
    return agent, model_settings, tool_names, tool_approval
