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
from co_cli.deps import CoDeps, CoConfig, ToolInfo, LoadPolicy, ToolSource
from co_cli._model_factory import ResolvedModel
from co_cli.context._history import (
    inject_opening_context,
    truncate_tool_results,
    compact_assistant_responses,
    detect_safety_issues,
    summarize_history_window,
)
from co_cli.tools.shell import run_shell_command
from co_cli.tools.obsidian import list_notes, read_note, search_notes
from co_cli.tools.google_drive import search_drive_files, read_drive_file
from co_cli.tools.google_gmail import list_gmail_emails, search_gmail_emails, create_gmail_draft
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.web import web_search, web_fetch
from co_cli.tools.memory import save_memory, list_memories, update_memory, append_memory, search_memories
from co_cli.memory.recall import load_always_on_memories
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
class ToolRegistry:
    """Immutable return value of build_tool_registry().

    Holds the native toolset, MCP toolsets (pre-built, not yet connected),
    and the combined tool_index (native + MCP after discovery).
    """
    toolset: AbstractToolset[CoDeps]
    mcp_toolsets: list
    tool_index: dict[str, ToolInfo]


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
) -> "tuple[AbstractToolset[CoDeps], dict[str, ToolInfo]]":
    """Build a FilteredToolset containing all tools for this config.

    Tools are registered into a FunctionToolset wrapped with a per-request filter.
    The filter uses per-tool LoadPolicy plus session.discovered_tools
    and runtime.resume_tool_names to decide visibility per API call.

    Domain tools (obsidian, google) are conditionally excluded when the relevant
    config paths are absent — they would fail at runtime regardless, so there is no
    point sending their schemas.

    Returns (filtered_toolset, native_index) where native_index maps each tool name
    to its ToolInfo metadata.
    """
    inner: FunctionToolset[CoDeps] = FunctionToolset()
    native_index: dict[str, ToolInfo] = {}

    def _reg(
        fn: Any,
        *,
        approval: bool = False,
        load: LoadPolicy,
        search_hint: str | None = None,
        integration: str | None = None,
        retries: int | None = None,
        max_result_size: int = 50_000,
    ) -> None:
        name = fn.__name__
        description = fn.__doc__.split("\n")[0].strip() if fn.__doc__ else fn.__name__
        kwargs: dict[str, Any] = {"requires_approval": approval}
        if retries is not None:
            kwargs["retries"] = retries
        inner.add_function(fn, **kwargs)
        native_index[name] = ToolInfo(
            name=name,
            description=description,
            approval=approval,
            source=ToolSource.NATIVE,
            load=load,
            integration=integration,
            search_hint=search_hint,
            max_result_size=max_result_size,
        )

    # --- Always-loaded tools ---
    _A = LoadPolicy.ALWAYS

    # Capability introspection and tool discovery
    _reg(check_capabilities, load=_A)
    _reg(search_tools, load=_A)

    # Session task tracking
    _reg(write_todos, load=_A)
    _reg(read_todos, load=_A)

    # Knowledge reads
    _reg(search_memories, load=_A)
    _reg(search_knowledge, load=_A)
    _reg(search_articles, load=_A)
    _reg(read_article, load=_A)
    _reg(list_memories, load=_A)

    # Workspace reads
    _reg(list_directory, load=_A)
    _reg(read_file, load=_A, max_result_size=80_000)
    _reg(find_in_files, load=_A)

    # Web
    _reg(web_search, load=_A, retries=3)
    _reg(web_fetch, load=_A, retries=3)

    # Execution
    _reg(run_shell_command, load=_A, max_result_size=30_000)

    # --- Deferred tools ---
    _D = LoadPolicy.DEFERRED

    # File write tools
    _reg(write_file, approval=True, load=_D, search_hint="create write new file", retries=1)
    _reg(edit_file, approval=True, load=_D, search_hint="modify patch update file", retries=1)

    # Knowledge write tools
    _reg(save_memory, approval=True, load=_D, search_hint="remember save note memory", retries=1)
    _reg(save_article, approval=True, load=_D, search_hint="save article knowledge", retries=1)
    _reg(update_memory, approval=True, load=_D, search_hint="update edit memory", retries=1)
    _reg(append_memory, approval=True, load=_D, search_hint="append add memory", retries=1)

    # Background task tools
    _reg(start_background_task, approval=True, load=_D, search_hint="background async long running task")
    _reg(check_task_status, load=_D, search_hint="background task status check")
    _reg(cancel_background_task, load=_D, search_hint="cancel stop background task")
    _reg(list_background_tasks, load=_D, search_hint="list background tasks")

    # Sub-agent tools — registered only when the role model is configured
    if config.role_models.get(ROLE_CODING):
        _reg(run_coding_subagent, load=_D, search_hint="coding sub-agent delegate code")
    if config.role_models.get(ROLE_RESEARCH):
        _reg(run_research_subagent, load=_D, search_hint="research sub-agent delegate search")
    if config.role_models.get(ROLE_ANALYSIS):
        _reg(run_analysis_subagent, load=_D, search_hint="analysis sub-agent delegate analyze")
    if config.role_models.get(ROLE_REASONING):
        _reg(run_reasoning_subagent, load=_D, search_hint="reasoning sub-agent delegate think")

    # Domain tools — conditional on config presence; excluded when integration absent
    if config.obsidian_vault_path:
        _reg(list_notes, load=_D, integration="obsidian", search_hint="obsidian notes list")
        _reg(search_notes, load=_D, integration="obsidian", search_hint="obsidian notes search")
        _reg(read_note, load=_D, integration="obsidian", search_hint="obsidian note read")

    if config.google_credentials_path:
        _reg(search_drive_files, load=_D, integration="google_drive", search_hint="google drive search files", retries=3)
        _reg(read_drive_file, load=_D, integration="google_drive", search_hint="google drive read file", retries=3)
        _reg(list_gmail_emails, load=_D, integration="google_gmail", search_hint="gmail email list inbox", retries=3)
        _reg(search_gmail_emails, load=_D, integration="google_gmail", search_hint="gmail email search", retries=3)
        _reg(list_calendar_events, load=_D, integration="google_calendar", search_hint="google calendar events list", retries=3)
        _reg(search_calendar_events, load=_D, integration="google_calendar", search_hint="google calendar events search", retries=3)
        _reg(create_gmail_draft, approval=True, load=_D, integration="google_gmail", search_hint="gmail email draft compose", retries=1)

    def _filter(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> bool:
        entry = ctx.deps.tool_index.get(tool_def.name)
        resume = ctx.deps.runtime.resume_tool_names

        if resume is not None:
            if tool_def.name in resume:
                return True
            if entry is not None and entry.load == LoadPolicy.ALWAYS:
                return True
            return False

        # Normal turn — unknown tools (not in tool_index) are hidden.
        # MCP tools are added to tool_index during bootstrap before the first agent.run().
        if entry is None:
            logger.warning("_filter: tool %r not in tool_index — hidden", tool_def.name)
            return False
        if entry.load == LoadPolicy.ALWAYS:
            return True
        return tool_def.name in ctx.deps.session.discovered_tools

    return inner.filtered(_filter), native_index


def build_tool_registry(config: CoConfig) -> ToolRegistry:
    """Build the tool registry from config.

    Pure config — no IO. Called once in create_deps().
    Returns native toolset, MCP toolsets (not yet connected), and native tool_index.
    MCP tool_index entries are added later by discover_mcp_tools().
    """
    filtered_toolset, native_index = _build_filtered_toolset(config)
    mcp_toolsets = _build_mcp_toolsets(config)
    return ToolRegistry(
        toolset=filtered_toolset,
        mcp_toolsets=mcp_toolsets,
        tool_index=native_index,
    )


def build_agent(
    *,
    config: CoConfig,
    model_registry: "ModelRegistry | None" = None,
    tool_registry: ToolRegistry | None = None,
) -> Agent[CoDeps, str | DeferredToolRequests]:
    """Build the main session Agent with model and settings baked in at construction.

    Args:
        config: Session config — static instructions, tool policy, MCP servers.
        model_registry: Pre-built registry for role→model lookup. When omitted,
            built from config internally (used by evals and tests that don't
            construct a registry).
        tool_registry: Pre-built tool registry. When omitted, built from config
            internally.
    """
    if model_registry is None:
        from co_cli._model_factory import ModelRegistry
        model_registry = ModelRegistry.from_config(config)
    reasoning_model = model_registry.get(
        ROLE_REASONING, ResolvedModel(model=None, settings=None)
    )

    if tool_registry is None:
        tool_registry = build_tool_registry(config)

    # Assemble static instructions (personality, rules, counter-steering) once at build time.
    from co_cli.prompts._assembly import build_static_instructions
    from co_cli.prompts.model_quirks._loader import normalize_model_name
    reasoning_entry = config.role_models.get(ROLE_REASONING)
    normalized_model = normalize_model_name(reasoning_entry.model) if reasoning_entry else ""
    static_instructions = build_static_instructions(config.llm_provider, normalized_model, config)

    # Static layer — set once at agent construction; does not change between turns.
    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        reasoning_model.model,
        deps_type=CoDeps,
        instructions=static_instructions,
        model_settings=reasoning_model.settings,
        retries=config.tool_retries,
        output_type=[str, DeferredToolRequests],
        history_processors=[
            truncate_tool_results,
            compact_assistant_responses,
            detect_safety_issues,
            inject_opening_context,
            summarize_history_window,
        ],
        toolsets=[tool_registry.toolset] + tool_registry.mcp_toolsets,
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
        entries = load_always_on_memories(memory_dir)
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
            ctx.deps.tool_index,
            ctx.deps.session.discovered_tools,
        )
        return prompt or ""

    return agent


async def discover_mcp_tools(
    mcp_toolsets: list, exclude: set[str]
) -> tuple[list[str], dict[str, str], dict[str, ToolInfo]]:
    """Discover MCP tool names by connecting to servers and listing tools.

    Each server self-connects on list_tools() (pydantic-ai lazy init).
    Returns (tool_names, errors, mcp_index) where errors maps server prefix to
    the error string for each server where list_tools() failed, and mcp_index maps
    tool name to ToolInfo metadata. Tool names exclude any in ``exclude``.
    MCP tools are deferred by default (load=LoadPolicy.DEFERRED).
    """
    from pydantic_ai.mcp import MCPServer

    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}
    mcp_index: dict[str, ToolInfo] = {}

    for toolset in mcp_toolsets:
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
                    mcp_index[name] = ToolInfo(
                        name=name,
                        description=t.description or "",
                        approval=approval,
                        source=ToolSource.MCP,
                        load=LoadPolicy.DEFERRED,
                        integration=prefix or None,
                        search_hint=prefix or None,
                    )
        except Exception as e:
            logger.warning(
                "MCP tool list failed for %r: %s", prefix or "(no prefix)", e
            )
            errors[prefix] = str(e)

    return sorted(mcp_tool_names), errors, mcp_index
