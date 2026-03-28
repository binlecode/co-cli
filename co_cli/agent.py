import logging
from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, DeferredToolRequests, RunContext

from co_cli.config import ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS, ROLE_REASONING
from co_cli.deps import CoDeps, CoConfig
from co_cli._model_factory import ResolvedModel
from co_cli.context._history import (
    inject_opening_context,
    truncate_tool_returns,
    detect_safety_issues,
    truncate_history_window,
)
from co_cli.prompts._assembly import assemble_prompt
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import list_notes, read_note, search_notes
from co_cli.tools.google_drive import search_drive_files, read_drive_file
from co_cli.tools.google_gmail import list_emails, search_emails, create_email_draft
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.web import web_search, web_fetch
from co_cli.tools.memory import save_memory, list_memories, update_memory, append_memory, search_memories, _load_always_on_memories
from co_cli.tools.articles import save_article, recall_article, read_article_detail, search_knowledge
from co_cli.tools.todo import todo_write, todo_read
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.files import list_directory, read_file, find_in_files, write_file, edit_file
from co_cli.tools.subagent import run_coder_subagent, run_research_subagent, run_analysis_subagent, run_thinking_subagent
from co_cli.tools.task_control import (
    start_background_task,
    check_task_status,
    cancel_background_task,
    list_background_tasks,
)

logger = logging.getLogger(__name__)




def build_agent(
    *,
    config: CoConfig,
    resolved: "ResolvedModel | None" = None,
) -> tuple[Agent[CoDeps, str | DeferredToolRequests], list[str], dict[str, bool]]:
    """Build the main session Agent with model and settings baked in at construction.

    Args:
        config: Session config — system prompt, tool policy, MCP servers.
        resolved: Pre-built reasoning model + inference settings. When omitted,
            resolved from ModelRegistry.from_config(config). Callers that already
            hold a resolved model (main.py) pass it explicitly to avoid building
            the registry twice and to reuse the same instance as primary_model.
    """
    if resolved is None:
        from co_cli._model_factory import ModelRegistry
        resolved = ModelRegistry.from_config(config).get(
            ROLE_REASONING, ResolvedModel(model=None, settings=None)
        )
    # Build MCP toolsets from config
    mcp_toolsets = []
    if config.mcp_servers:
        from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP, MCPServerSSE

        for name, cfg in config.mcp_servers.items():
            if cfg.url:
                # HTTP transport — SSE when URL ends with /sse, else StreamableHTTP
                if cfg.url.rstrip("/").endswith("/sse"):
                    server = MCPServerSSE(cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout)
                else:
                    server = MCPServerStreamableHTTP(cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout)
            else:
                env = dict(cfg.env) if cfg.env else {}
                server = MCPServerStdio(
                    cfg.command,
                    args=cfg.args,
                    timeout=cfg.timeout,
                    env=env or None,
                    tool_prefix=cfg.prefix or name,
                )
            if cfg.approval == "ask":
                server = server.approval_required()
            mcp_toolsets.append(server)

    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        resolved.model,
        deps_type=CoDeps,
        instructions=config.system_prompt,
        model_settings=resolved.settings,
        retries=config.tool_retries,
        output_type=[str, DeferredToolRequests],
        history_processors=[
            truncate_tool_returns,
            detect_safety_issues,
            inject_opening_context,
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
    def add_always_on_memories(ctx: RunContext[CoDeps]) -> str:
        """Inject always_on memories as standing context every turn."""
        memory_dir = ctx.deps.config.memory_dir
        entries = _load_always_on_memories(memory_dir)
        if not entries:
            return ""
        max_chars = ctx.deps.config.memory_injection_max_chars
        text = "\n\n".join(e.content for e in entries)[:max_chars]
        return f"Standing context:\n{text}"

    @agent.instructions
    def add_personality_memories(ctx: RunContext[CoDeps]) -> str:
        """Inject personality-context memories for relationship continuity."""
        if not ctx.deps.config.personality:
            return ""
        from co_cli.prompts.personalities._injector import _load_personality_memories
        return _load_personality_memories()

    tool_approvals: dict[str, bool] = {}

    def _register(fn, requires_approval: bool, retries: int | None = None) -> None:
        kwargs: dict[str, Any] = {"requires_approval": requires_approval}
        if retries is not None:
            kwargs["retries"] = retries
        agent.tool(fn, **kwargs)
        tool_approvals[fn.__name__] = requires_approval

    # Background task management
    _register(start_background_task, True)
    _register(check_task_status, False)
    _register(cancel_background_task, False)
    _register(list_background_tasks, False)

    # Capability introspection — no approval (read-only, no side effects)
    _register(check_capabilities, False)

    # Sub-agent tools — registered only when the role model is configured
    if config.role_models.get(ROLE_CODING):
        _register(run_coder_subagent, False)
    if config.role_models.get(ROLE_RESEARCH):
        _register(run_research_subagent, False)
    if config.role_models.get(ROLE_ANALYSIS):
        _register(run_analysis_subagent, False)
    if config.role_models.get(ROLE_REASONING):
        _register(run_thinking_subagent, False)

    # Native file tools — write-once tier: retries=1 (a second attempt on failure is safe)
    _register(list_directory, False)
    _register(read_file, False)
    _register(find_in_files, False)
    _register(write_file, True, retries=1)
    _register(edit_file, True, retries=1)

    # Shell: fine-grained policy lives inside the tool (DENY/safe-prefix/ask).
    # Agent-layer approval is False; the tool raises ApprovalRequired for commands
    # that need user confirmation.
    _register(run_shell_command, False)
    # Write-once tier: single retry for transient failures
    _register(create_email_draft, True, retries=1)
    _register(save_memory, True, retries=1)
    _register(save_article, True, retries=1)
    _register(update_memory, True, retries=1)
    _register(append_memory, True, retries=1)

    # Session task tracking — no approval (in-memory only, no external side effects)
    _register(todo_write, False)
    _register(todo_read, False)

    # Read-only tools — no approval needed
    _register(list_memories, False)
    _register(search_memories, False)
    _register(read_article_detail, False)
    _register(search_knowledge, False)
    _register(list_notes, False)
    _register(search_notes, False)
    _register(read_note, False)
    _register(recall_article, False)
    # Network tier: retries=3 for transient connectivity failures
    _register(search_drive_files, False, retries=3)
    _register(read_drive_file, False, retries=3)
    _register(list_emails, False, retries=3)
    _register(search_emails, False, retries=3)
    _register(list_calendar_events, False, retries=3)
    _register(search_calendar_events, False, retries=3)
    policy = config.web_policy
    search_approval = policy.search == "ask"
    fetch_approval = policy.fetch == "ask"
    # Network tier: retries=3 for transient connectivity failures
    _register(web_search, search_approval, retries=3)
    _register(web_fetch, fetch_approval, retries=3)

    return agent, list(tool_approvals.keys()), tool_approvals


async def discover_mcp_tools(
    agent: Agent, exclude: set[str]
) -> tuple[list[str], dict[str, str]]:
    """Discover MCP tool names from connected servers (after async with agent).

    Returns a tuple of (tool_names, errors) where errors maps server prefix to
    the error string for each server where list_tools() failed. Tool names
    exclude any names already in ``exclude``.
    """
    from pydantic_ai.mcp import MCPServer

    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}

    for toolset in agent.toolsets:
        # Unwrap approval wrappers to reach the MCPServer base instance
        inner = getattr(toolset, "wrapped", toolset)
        if not isinstance(inner, MCPServer):
            continue
        prefix = inner.tool_prefix or ""
        try:
            tools = await inner.list_tools()
            for t in tools:
                name = f"{prefix}_{t.name}" if prefix else t.name
                if name not in exclude:
                    mcp_tool_names.append(name)
        except Exception as e:
            logger.warning(
                "MCP tool list failed for %r: %s", prefix or "(no prefix)", e
            )
            errors[prefix] = str(e)

    return sorted(mcp_tool_names), errors

