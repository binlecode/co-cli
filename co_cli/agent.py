import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co_cli._model_factory import ModelRegistry

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset
from pydantic_ai.tools import ToolDefinition

from co_cli.config import ROLE_CODING, ROLE_RESEARCH, ROLE_ANALYSIS, ROLE_REASONING
from co_cli.deps import CoDeps, CoConfig, ToolConfig
from co_cli._model_factory import ResolvedModel
from co_cli.context._history import (
    inject_opening_context,
    truncate_tool_returns,
    detect_safety_issues,
    truncate_history_window,
)
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import list_notes, read_note, search_notes
from co_cli.tools.google_drive import search_drive_files, read_drive_file
from co_cli.tools.google_gmail import list_gmail_emails, search_gmail_emails, create_gmail_draft
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.web import web_search, web_fetch
from co_cli.tools.memory import save_memory, list_memories, update_memory, append_memory, search_memories, _load_always_on_memories
from co_cli.tools.articles import save_article, search_articles, read_article, search_knowledge
from co_cli.tools.todo import write_todos, read_todos
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.tool_search import search_tools
from co_cli.tools.files import list_directory, read_file, find_in_files, write_file, edit_file
from co_cli.tools.subagent import run_coding_subagent, run_research_subagent, run_analysis_subagent, run_reasoning_subagent
from co_cli.tools.task_control import (
    start_background_task,
    check_task_status,
    cancel_background_task,
    list_background_tasks,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentCapabilityResult:
    """Immutable return value of build_agent()."""
    agent: Agent[CoDeps, str | DeferredToolRequests]
    tool_index: dict[str, ToolConfig] = field(default_factory=dict)


def _build_mcp_toolsets(config: CoConfig) -> list:
    """Build pydantic-ai MCP toolset objects from config.mcp_servers."""
    if not config.mcp_servers:
        return []
    from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP, MCPServerSSE

    mcp_toolsets = []
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
    return mcp_toolsets


def _build_filtered_toolset(
    config: CoConfig,
) -> "tuple[AbstractToolset[CoDeps], dict[str, ToolConfig]]":
    """Build a FilteredToolset containing all tools for this config.

    Tools are registered into a FunctionToolset wrapped with a per-request filter.
    The filter uses per-tool always_load/should_defer flags plus session.discovered_tools
    and runtime.resume_tool_names to decide visibility per API call.

    Domain tools (obsidian, google) are conditionally excluded when the relevant
    config paths are absent — they would fail at runtime regardless, so there is no
    point sending their schemas.

    Returns (filtered_toolset, native_index) where native_index maps each tool name
    to its ToolConfig metadata.
    """
    inner: FunctionToolset[CoDeps] = FunctionToolset()
    native_index: dict[str, ToolConfig] = {}

    def _reg(
        fn: Any,
        *,
        approval: bool = False,
        always_load: bool = False,
        should_defer: bool = False,
        search_hint: str | None = None,
        integration: str | None = None,
        retries: int | None = None,
    ) -> None:
        name = fn.__name__
        description = fn.__doc__.split("\n")[0].strip() if fn.__doc__ else fn.__name__
        kwargs: dict[str, Any] = {"requires_approval": approval}
        if retries is not None:
            kwargs["retries"] = retries
        inner.add_function(fn, **kwargs)
        native_index[name] = ToolConfig(
            name=name,
            description=description,
            approval=approval,
            source="native",
            integration=integration,
            always_load=always_load,
            should_defer=should_defer,
            search_hint=search_hint,
        )

    # --- Always-loaded tools ---

    # Capability introspection and tool discovery
    _reg(check_capabilities, approval=False, always_load=True)
    _reg(search_tools, approval=False, always_load=True)

    # Session task tracking
    _reg(write_todos, approval=False, always_load=True)
    _reg(read_todos, approval=False, always_load=True)

    # Knowledge reads
    _reg(search_memories, approval=False, always_load=True)
    _reg(search_knowledge, approval=False, always_load=True)
    _reg(search_articles, approval=False, always_load=True)
    _reg(read_article, approval=False, always_load=True)
    _reg(list_memories, approval=False, always_load=True)

    # Workspace reads
    _reg(list_directory, approval=False, always_load=True)
    _reg(read_file, approval=False, always_load=True)
    _reg(find_in_files, approval=False, always_load=True)

    # Web
    _reg(web_search, approval=False, always_load=True, retries=3)
    _reg(web_fetch, approval=False, always_load=True, retries=3)

    # Execution
    _reg(run_shell_command, approval=False, always_load=True)

    # --- Deferred tools ---

    # File write tools
    _reg(write_file, approval=True, should_defer=True, search_hint="create write new file", retries=1)
    _reg(edit_file, approval=True, should_defer=True, search_hint="modify patch update file", retries=1)

    # Knowledge write tools
    _reg(save_memory, approval=True, should_defer=True, search_hint="remember save note memory", retries=1)
    _reg(save_article, approval=True, should_defer=True, search_hint="save article knowledge", retries=1)
    _reg(update_memory, approval=True, should_defer=True, search_hint="update edit memory", retries=1)
    _reg(append_memory, approval=True, should_defer=True, search_hint="append add memory", retries=1)

    # Background task tools
    _reg(start_background_task, approval=True, should_defer=True, search_hint="background async long running task")
    _reg(check_task_status, approval=False, should_defer=True, search_hint="background task status check")
    _reg(cancel_background_task, approval=False, should_defer=True, search_hint="cancel stop background task")
    _reg(list_background_tasks, approval=False, should_defer=True, search_hint="list background tasks")

    # Sub-agent tools — registered only when the role model is configured
    if config.role_models.get(ROLE_CODING):
        _reg(run_coding_subagent, approval=False, should_defer=True, search_hint="coding sub-agent delegate code")
    if config.role_models.get(ROLE_RESEARCH):
        _reg(run_research_subagent, approval=False, should_defer=True, search_hint="research sub-agent delegate search")
    if config.role_models.get(ROLE_ANALYSIS):
        _reg(run_analysis_subagent, approval=False, should_defer=True, search_hint="analysis sub-agent delegate analyze")
    if config.role_models.get(ROLE_REASONING):
        _reg(run_reasoning_subagent, approval=False, should_defer=True, search_hint="reasoning sub-agent delegate think")

    # Domain tools — conditional on config presence; excluded when integration absent
    if config.obsidian_vault_path:
        _reg(list_notes, approval=False, should_defer=True, integration="obsidian", search_hint="obsidian notes list")
        _reg(search_notes, approval=False, should_defer=True, integration="obsidian", search_hint="obsidian notes search")
        _reg(read_note, approval=False, should_defer=True, integration="obsidian", search_hint="obsidian note read")

    if config.google_credentials_path:
        _reg(search_drive_files, approval=False, should_defer=True, integration="google_drive", search_hint="google drive search files", retries=3)
        _reg(read_drive_file, approval=False, should_defer=True, integration="google_drive", search_hint="google drive read file", retries=3)
        _reg(list_gmail_emails, approval=False, should_defer=True, integration="google_gmail", search_hint="gmail email list inbox", retries=3)
        _reg(search_gmail_emails, approval=False, should_defer=True, integration="google_gmail", search_hint="gmail email search", retries=3)
        _reg(list_calendar_events, approval=False, should_defer=True, integration="google_calendar", search_hint="google calendar events list", retries=3)
        _reg(search_calendar_events, approval=False, should_defer=True, integration="google_calendar", search_hint="google calendar events search", retries=3)
        _reg(create_gmail_draft, approval=True, should_defer=True, integration="google_gmail", search_hint="gmail email draft compose", retries=1)

    def _filter(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> bool:
        entry = ctx.deps.services.tool_index.get(tool_def.name)
        resume = ctx.deps.runtime.resume_tool_names

        if resume is not None:
            if tool_def.name in resume:
                return True
            if entry is not None and entry.always_load:
                return True
            return False

        # Normal turn
        if entry is None:
            return True
        if entry.always_load:
            return True
        return tool_def.name in ctx.deps.session.discovered_tools

    return inner.filtered(_filter), native_index


_TASK_AGENT_SYSTEM_PROMPT: str = (
    "You have received results for tool calls that the user approved. "
    "Process these results and respond to the user concisely and directly."
)


def build_agent(
    *,
    config: CoConfig,
    model_registry: "ModelRegistry | None" = None,
) -> AgentCapabilityResult:
    """Build the main session Agent with model and settings baked in at construction.

    Args:
        config: Session config — static instructions, tool policy, MCP servers.
        model_registry: Pre-built registry for role→model lookup. When omitted,
            built from config internally (used by evals and tests that don't
            construct a registry).
    """
    if model_registry is None:
        from co_cli._model_factory import ModelRegistry
        model_registry = ModelRegistry.from_config(config)
    resolved = model_registry.get(
        ROLE_REASONING, ResolvedModel(model=None, settings=None)
    )

    # Assemble static instructions (personality, rules, counter-steering) once at build time.
    from co_cli.prompts._assembly import build_static_instructions
    from co_cli.prompts.model_quirks._loader import normalize_model_name
    reasoning_entry = config.role_models.get(ROLE_REASONING)
    normalized_model = normalize_model_name(reasoning_entry.model) if reasoning_entry else ""
    static_instructions = build_static_instructions(config.llm_provider, normalized_model, config)

    mcp_toolsets = _build_mcp_toolsets(config)
    filtered_toolset, native_index = _build_filtered_toolset(config)

    # Static layer — set once at agent construction; does not change between turns.
    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        resolved.model,
        deps_type=CoDeps,
        instructions=static_instructions,
        model_settings=resolved.settings,
        retries=config.tool_retries,
        output_type=[str, DeferredToolRequests],
        history_processors=[
            truncate_tool_returns,
            detect_safety_issues,
            inject_opening_context,
            truncate_history_window,
        ],
        toolsets=[filtered_toolset] + mcp_toolsets,
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

    @agent.instructions
    def add_deferred_tool_prompt(ctx: RunContext[CoDeps]) -> str:
        """Inject deferred-tool awareness so the model knows to call search_tools."""
        from co_cli.context._deferred_tool_prompt import build_deferred_tool_prompt
        prompt = build_deferred_tool_prompt(
            ctx.deps.services.tool_index,
            ctx.deps.session.discovered_tools,
        )
        return prompt or ""

    return AgentCapabilityResult(
        agent=agent,
        tool_index=native_index,
    )


def build_task_agent(
    *,
    config: CoConfig,
    resolved: "ResolvedModel",
) -> AgentCapabilityResult:
    """Build the lightweight task agent for approval resume turns.

    No personality, no date injection, no project instructions, no history processors.
    Same tools and approval flags as the main agent. Used by _run_approval_loop to
    resume approved deferred tool calls without the full main agent context overhead.
    """
    mcp_toolsets = _build_mcp_toolsets(config)
    filtered_toolset, native_index = _build_filtered_toolset(config)
    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        resolved.model,
        deps_type=CoDeps,
        instructions=_TASK_AGENT_SYSTEM_PROMPT,
        model_settings=resolved.settings,
        retries=config.tool_retries,
        output_type=[str, DeferredToolRequests],
        toolsets=[filtered_toolset] + mcp_toolsets,
    )
    return AgentCapabilityResult(
        agent=agent,
        tool_index=native_index,
    )


async def discover_mcp_tools(
    agent: Agent, exclude: set[str]
) -> tuple[list[str], dict[str, str], dict[str, ToolConfig]]:
    """Discover MCP tool names from connected servers (after async with agent).

    Returns a tuple of (tool_names, errors, mcp_index) where errors maps server prefix to
    the error string for each server where list_tools() failed, and mcp_index maps tool
    name to ToolConfig metadata. Tool names exclude any names already in ``exclude``.
    MCP tools are deferred by default (should_defer=True).
    """
    from pydantic_ai.mcp import MCPServer

    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}
    mcp_index: dict[str, ToolConfig] = {}

    for toolset in agent.toolsets:
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
                    approval = inner is not toolset
                    mcp_index[name] = ToolConfig(
                        name=name,
                        description=t.description or "",
                        approval=approval,
                        source="mcp",
                        should_defer=True,
                    )
        except Exception as e:
            logger.warning(
                "MCP tool list failed for %r: %s", prefix or "(no prefix)", e
            )
            errors[prefix] = str(e)

    return sorted(mcp_tool_names), errors, mcp_index
