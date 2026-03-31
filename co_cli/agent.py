import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

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
from co_cli.tools.files import list_directory, read_file, find_in_files, write_file, edit_file
from co_cli.tools.subagent import run_coding_subagent, run_research_subagent, run_analysis_subagent, run_reasoning_subagent
from co_cli.tools.task_control import (
    start_background_task,
    check_task_status,
    cancel_background_task,
    list_background_tasks,
)

logger = logging.getLogger(__name__)

# Tools that must remain visible on every segment, including approval-resume turns.
# The model needs these to report status, manage session todos, and check capabilities
# even when the filter is narrowed to only the deferred tool names.
_ALWAYS_ON_TOOL_NAMES: frozenset[str] = frozenset({
    "check_capabilities",
    "read_todos",
    "write_todos",
})


@dataclass(frozen=True)
class AgentCapabilityResult:
    """Immutable return value of build_agent()."""
    agent: Agent[CoDeps, str | DeferredToolRequests]
    tool_names: list[str]
    tool_approvals: dict[str, bool]
    tool_catalog: dict[str, ToolConfig] = field(default_factory=dict)


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
) -> "tuple[AbstractToolset[CoDeps], dict[str, bool], dict[str, ToolConfig]]":
    """Build a FilteredToolset containing all tools for this config.

    Tools are registered into a FunctionToolset and wrapped with a FilteredToolset
    that reads deps.runtime.active_tool_filter per request. When the filter is None
    (main agent turns), all tools are visible. When set (approval-resume turns), only
    the named tools plus _ALWAYS_ON_TOOL_NAMES are sent to the model.

    Domain tools (obsidian, google) are conditionally excluded when the relevant
    config paths are absent — they would fail at runtime regardless, so there is no
    point sending their schemas.

    Returns (filtered_toolset, tool_approvals, native_catalog) where tool_approvals maps
    each registered tool name to its requires_approval flag, and native_catalog maps
    each tool name to its ToolConfig metadata.
    """
    inner: FunctionToolset[CoDeps] = FunctionToolset()
    tool_approvals: dict[str, bool] = {}
    native_catalog: dict[str, ToolConfig] = {}

    def _reg(
        fn: Any,
        *,
        approval: bool = False,
        family: str,
        integration: str | None = None,
        retries: int | None = None,
    ) -> None:
        name = fn.__name__
        kwargs: dict[str, Any] = {"requires_approval": approval}
        if retries is not None:
            kwargs["retries"] = retries
        inner.add_function(fn, **kwargs)
        tool_approvals[name] = approval
        native_catalog[name] = ToolConfig(
            name=name,
            source="native",
            family=family,
            approval=approval,
            integration=integration,
        )

    # Background task management
    _reg(start_background_task, approval=True, family="workflow")
    _reg(check_task_status, approval=False, family="workflow")
    _reg(cancel_background_task, approval=False, family="workflow")
    _reg(list_background_tasks, approval=False, family="workflow")

    # Capability introspection — no approval (read-only, no side effects)
    _reg(check_capabilities, approval=False, family="system")

    # Sub-agent tools — registered only when the role model is configured
    if config.role_models.get(ROLE_CODING):
        _reg(run_coding_subagent, approval=False, family="delegation")
    if config.role_models.get(ROLE_RESEARCH):
        _reg(run_research_subagent, approval=False, family="delegation")
    if config.role_models.get(ROLE_ANALYSIS):
        _reg(run_analysis_subagent, approval=False, family="delegation")
    if config.role_models.get(ROLE_REASONING):
        _reg(run_reasoning_subagent, approval=False, family="delegation")

    # Native file tools — write-once tier: retries=1 (a second attempt on failure is safe)
    _reg(list_directory, approval=False, family="workspace")
    _reg(read_file, approval=False, family="workspace")
    _reg(find_in_files, approval=False, family="workspace")
    _reg(write_file, approval=True, family="workspace", retries=1)
    _reg(edit_file, approval=True, family="workspace", retries=1)

    # Shell: fine-grained policy lives inside the tool (DENY/safe-prefix/ask).
    # Agent-layer approval is False; the tool raises ApprovalRequired for commands
    # that need user confirmation.
    _reg(run_shell_command, approval=False, family="execution")
    # Write-once tier: single retry for transient failures
    _reg(save_memory, approval=True, family="knowledge", retries=1)
    _reg(save_article, approval=True, family="knowledge", retries=1)
    _reg(update_memory, approval=True, family="knowledge", retries=1)
    _reg(append_memory, approval=True, family="knowledge", retries=1)

    # Session task tracking — no approval (in-memory only, no external side effects)
    _reg(write_todos, approval=False, family="workflow")
    _reg(read_todos, approval=False, family="workflow")

    # Read-only tools — no approval needed
    _reg(list_memories, approval=False, family="knowledge")
    _reg(search_memories, approval=False, family="knowledge")
    _reg(read_article, approval=False, family="knowledge")
    _reg(search_knowledge, approval=False, family="knowledge")
    _reg(search_articles, approval=False, family="knowledge")

    # Domain tools — conditional on config presence; excluded when integration absent
    if config.obsidian_vault_path:
        _reg(list_notes, approval=False, family="connectors", integration="obsidian")
        _reg(search_notes, approval=False, family="connectors", integration="obsidian")
        _reg(read_note, approval=False, family="connectors", integration="obsidian")

    if config.google_credentials_path:
        # Network tier: retries=3 for transient connectivity failures
        _reg(search_drive_files, approval=False, family="connectors", integration="google_drive", retries=3)
        _reg(read_drive_file, approval=False, family="connectors", integration="google_drive", retries=3)
        _reg(list_gmail_emails, approval=False, family="connectors", integration="google_gmail", retries=3)
        _reg(search_gmail_emails, approval=False, family="connectors", integration="google_gmail", retries=3)
        _reg(list_calendar_events, approval=False, family="connectors", integration="google_calendar", retries=3)
        _reg(search_calendar_events, approval=False, family="connectors", integration="google_calendar", retries=3)
        _reg(create_gmail_draft, approval=True, family="connectors", integration="google_gmail", retries=1)

    policy = config.web_policy
    # Network tier: retries=3 for transient connectivity failures
    _reg(web_search, approval=policy.search == "ask", family="web", retries=3)
    _reg(web_fetch, approval=policy.fetch == "ask", family="web", retries=3)

    def _filter(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> bool:
        f = ctx.deps.runtime.active_tool_filter
        return f is None or tool_def.name in f

    # .filtered() is the correct pydantic-ai 1.73 API for per-request tool filtering — no `PreparedToolset` or equivalent exists.
    return inner.filtered(_filter), tool_approvals, native_catalog


_TASK_AGENT_SYSTEM_PROMPT: str = (
    "You have received results for tool calls that the user approved. "
    "Process these results and respond to the user concisely and directly."
)


def build_agent(
    *,
    config: CoConfig,
    resolved: "ResolvedModel | None" = None,
) -> AgentCapabilityResult:
    """Build the main session Agent with model and settings baked in at construction.

    Args:
        config: Session config — static instructions, tool policy, MCP servers.
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
    mcp_toolsets = _build_mcp_toolsets(config)
    filtered_toolset, tool_approvals, native_catalog = _build_filtered_toolset(config)

    # Static layer — set once at agent construction; does not change between turns.
    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        resolved.model,
        deps_type=CoDeps,
        instructions=config.static_instructions,
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

    return AgentCapabilityResult(
        agent=agent,
        tool_names=list(tool_approvals.keys()),
        tool_approvals=tool_approvals,
        tool_catalog=native_catalog,
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
    filtered_toolset, tool_approvals, native_catalog = _build_filtered_toolset(config)
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
        tool_names=list(tool_approvals.keys()),
        tool_approvals=tool_approvals,
        tool_catalog=native_catalog,
    )


async def discover_mcp_tools(
    agent: Agent, exclude: set[str]
) -> tuple[list[str], dict[str, str], dict[str, ToolConfig]]:
    """Discover MCP tool names from connected servers (after async with agent).

    Returns a tuple of (tool_names, errors, mcp_catalog) where errors maps server prefix to
    the error string for each server where list_tools() failed, and mcp_catalog maps tool
    name to ToolConfig metadata. Tool names exclude any names already in ``exclude``.
    """
    from pydantic_ai.mcp import MCPServer

    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}
    mcp_catalog: dict[str, ToolConfig] = {}

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
                    approval = inner is not toolset
                    mcp_catalog[name] = ToolConfig(
                        name=name,
                        source="mcp",
                        family="connectors",
                        approval=approval,
                    )
        except Exception as e:
            logger.warning(
                "MCP tool list failed for %r: %s", prefix or "(no prefix)", e
            )
            errors[prefix] = str(e)

    return sorted(mcp_tool_names), errors, mcp_catalog
