import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co_cli._model_factory import LlmModel

from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, DeferredLoadingToolset, FunctionToolset
from pydantic_ai.toolsets.combined import CombinedToolset

from co_cli.config._core import Settings
from co_cli.context._history import (
    compact_assistant_responses,
    detect_safety_issues,
    inject_opening_context,
    summarize_history_window,
    truncate_tool_results,
)
from co_cli.context._tool_lifecycle import CoToolLifecycle
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.memory.recall import load_always_on_memories
from co_cli.tools.articles import read_article, save_article, search_articles, search_knowledge
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.files import edit_file, find_in_files, list_directory, read_file, write_file
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.google_drive import read_drive_file, search_drive_files
from co_cli.tools.google_gmail import create_gmail_draft, list_gmail_emails, search_gmail_emails
from co_cli.tools.memory import (
    append_memory,
    list_memories,
    save_memory,
    search_memories,
    update_memory,
)
from co_cli.tools.obsidian import list_notes, read_note, search_notes
from co_cli.tools.shell import run_shell_command
from co_cli.tools.subagent import (
    run_analysis_subagent,
    run_coding_subagent,
    run_reasoning_subagent,
    run_research_subagent,
)
from co_cli.tools.task_control import (
    cancel_background_task,
    check_task_status,
    list_background_tasks,
    start_background_task,
)
from co_cli.tools.todo import read_todos, write_todos
from co_cli.tools.web import web_fetch, web_search

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolRegistry:
    """Immutable return value of build_tool_registry().

    Holds the combined filtered toolset (native + MCP, approval-resume filter applied),
    the raw MCP toolsets (for bootstrap lifecycle management), and the tool_index
    (native entries; MCP entries added later by discover_mcp_tools()).
    """

    toolset: AbstractToolset[CoDeps]
    mcp_toolsets: list
    tool_index: dict[str, ToolInfo]


def _approval_resume_filter(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> bool:
    """Filter for approval-resume narrowing only.

    Normal turns: pass all unconditionally — SDK ToolSearchToolset handles
    deferred visibility via defer_loading.
    Resume turns: narrow to approved tools + always-visible tools.
    Applies uniformly to native and MCP tools.
    """
    resume = ctx.deps.runtime.resume_tool_names
    if resume is None:
        return True
    if tool_def.name in resume:
        return True
    entry = ctx.deps.tool_index.get(tool_def.name)
    return entry is not None and entry.visibility == VisibilityPolicyEnum.ALWAYS


def _build_mcp_toolsets(config: Settings) -> list:
    """Build MCP toolsets wrapped with DeferredLoadingToolset for SDK-native discovery."""
    if not config.mcp_servers:
        return []
    from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio, MCPServerStreamableHTTP

    mcp_toolsets = []
    for name, cfg in config.mcp_servers.items():
        if cfg.url:
            # HTTP transport — SSE when URL ends with /sse, else StreamableHTTP
            if cfg.url.rstrip("/").endswith("/sse"):
                server = MCPServerSSE(cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout)
            else:
                server = MCPServerStreamableHTTP(
                    cfg.url, tool_prefix=cfg.prefix or name, timeout=cfg.timeout
                )
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
        mcp_toolsets.append(DeferredLoadingToolset(server))
    return mcp_toolsets


def _build_native_toolset(
    config: Settings,
) -> "tuple[FunctionToolset[CoDeps], dict[str, ToolInfo]]":
    """Build an unfiltered FunctionToolset with per-tool defer_loading.

    Tools are registered with defer_loading derived from VisibilityPolicyEnum. The SDK's
    ToolSearchToolset (auto-added by Agent) handles deferred visibility.

    Domain tools (obsidian, google) are conditionally excluded when the relevant
    config paths are absent — they would fail at runtime regardless.

    Returns (native_toolset, native_index) where native_index maps each tool name
    to its ToolInfo metadata.
    """
    native_toolset: FunctionToolset[CoDeps] = FunctionToolset()
    native_index: dict[str, ToolInfo] = {}

    def _register_tool(
        fn: Any,
        *,
        approval: bool = False,
        visibility: VisibilityPolicyEnum,
        integration: str | None = None,
        retries: int | None = None,
        max_result_size: int = 50_000,
    ) -> None:
        name = fn.__name__
        description = fn.__doc__.split("\n")[0].strip() if fn.__doc__ else fn.__name__
        kwargs: dict[str, Any] = {
            "requires_approval": approval,
            "defer_loading": visibility == VisibilityPolicyEnum.DEFERRED,
        }
        if retries is not None:
            kwargs["retries"] = retries
        native_toolset.add_function(fn, **kwargs)
        native_index[name] = ToolInfo(
            name=name,
            description=description,
            approval=approval,
            source=ToolSourceEnum.NATIVE,
            visibility=visibility,
            integration=integration,
            max_result_size=max_result_size,
        )

    # --- Always-visible tools (defer_loading=False) ---
    _always_visible = VisibilityPolicyEnum.ALWAYS

    # Capability introspection
    _register_tool(check_capabilities, visibility=_always_visible)

    # Session task tracking
    _register_tool(write_todos, visibility=_always_visible)
    _register_tool(read_todos, visibility=_always_visible)

    # Knowledge reads
    _register_tool(search_memories, visibility=_always_visible)
    _register_tool(search_knowledge, visibility=_always_visible)
    _register_tool(search_articles, visibility=_always_visible)
    _register_tool(read_article, visibility=_always_visible)
    _register_tool(list_memories, visibility=_always_visible)

    # Workspace reads
    _register_tool(list_directory, visibility=_always_visible)
    _register_tool(read_file, visibility=_always_visible, max_result_size=80_000)
    _register_tool(find_in_files, visibility=_always_visible)

    # Web
    _register_tool(web_search, visibility=_always_visible, retries=3)
    _register_tool(web_fetch, visibility=_always_visible, retries=3)

    # Execution
    _register_tool(run_shell_command, visibility=_always_visible, max_result_size=30_000)

    # --- Deferred tools (defer_loading=True, discovered via SDK search_tools) ---
    _deferred_visible = VisibilityPolicyEnum.DEFERRED

    # File write tools
    _register_tool(write_file, approval=True, visibility=_deferred_visible, retries=1)
    _register_tool(edit_file, approval=True, visibility=_deferred_visible, retries=1)

    # Knowledge write tools
    _register_tool(save_memory, approval=True, visibility=_deferred_visible, retries=1)
    _register_tool(save_article, approval=True, visibility=_deferred_visible, retries=1)
    _register_tool(update_memory, approval=True, visibility=_deferred_visible, retries=1)
    _register_tool(append_memory, approval=True, visibility=_deferred_visible, retries=1)

    # Background task tools
    _register_tool(start_background_task, approval=True, visibility=_deferred_visible)
    _register_tool(check_task_status, visibility=_deferred_visible)
    _register_tool(cancel_background_task, visibility=_deferred_visible)
    _register_tool(list_background_tasks, visibility=_deferred_visible)

    # Sub-agent tools
    _register_tool(run_coding_subagent, visibility=_deferred_visible)
    _register_tool(run_research_subagent, visibility=_deferred_visible)
    _register_tool(run_analysis_subagent, visibility=_deferred_visible)
    _register_tool(run_reasoning_subagent, visibility=_deferred_visible)

    # Domain tools — conditional on config presence; excluded when integration absent
    if config.obsidian_vault_path:
        _register_tool(list_notes, visibility=_deferred_visible, integration="obsidian")
        _register_tool(search_notes, visibility=_deferred_visible, integration="obsidian")
        _register_tool(read_note, visibility=_deferred_visible, integration="obsidian")

    if config.google_credentials_path:
        _register_tool(
            search_drive_files, visibility=_deferred_visible, integration="google_drive", retries=3
        )
        _register_tool(
            read_drive_file, visibility=_deferred_visible, integration="google_drive", retries=3
        )
        _register_tool(
            list_gmail_emails, visibility=_deferred_visible, integration="google_gmail", retries=3
        )
        _register_tool(
            search_gmail_emails,
            visibility=_deferred_visible,
            integration="google_gmail",
            retries=3,
        )
        _register_tool(
            list_calendar_events,
            visibility=_deferred_visible,
            integration="google_calendar",
            retries=3,
        )
        _register_tool(
            search_calendar_events,
            visibility=_deferred_visible,
            integration="google_calendar",
            retries=3,
        )
        _register_tool(
            create_gmail_draft,
            approval=True,
            visibility=_deferred_visible,
            integration="google_gmail",
            retries=1,
        )

    return native_toolset, native_index


def build_tool_registry(config: Settings) -> ToolRegistry:
    """Build the tool registry from config.

    Pure config — no IO. Called once in create_deps().
    Combines native and MCP toolsets under a single approval-resume filter.
    MCP tool_index entries are added later by discover_mcp_tools().
    """
    native_toolset, native_index = _build_native_toolset(config)
    mcp_toolsets = _build_mcp_toolsets(config)

    # Combine all toolsets under one filter so approval-resume narrowing
    # applies uniformly to native and MCP tools.
    combined = CombinedToolset([native_toolset, *mcp_toolsets])
    filtered = combined.filtered(_approval_resume_filter)

    return ToolRegistry(
        toolset=filtered,
        mcp_toolsets=mcp_toolsets,
        tool_index=native_index,
    )


def build_agent(
    *,
    config: Settings,
    model: "LlmModel | None" = None,
    tool_registry: ToolRegistry | None = None,
) -> Agent[CoDeps, str | DeferredToolRequests]:
    """Build the main session Agent with model and settings baked in at construction.

    Args:
        config: Session config — static instructions, tool policy, MCP servers.
        model: Pre-built LlmModel from build_model(). When omitted,
            built from config internally (used by evals and tests).
        tool_registry: Pre-built tool registry. When omitted, built from config
            internally.
    """
    if model is None:
        from co_cli._model_factory import build_model

        model = build_model(config.llm)

    if tool_registry is None:
        tool_registry = build_tool_registry(config)

    # Assemble static instructions (personality, rules, counter-steering) once at build time.
    from co_cli.prompts._assembly import build_static_instructions
    from co_cli.prompts.model_quirks._loader import normalize_model_name

    normalized_model = normalize_model_name(config.llm.model)
    static_instructions = build_static_instructions(config.llm.provider, normalized_model, config)

    # Static layer — set once at agent construction; does not change between turns.
    # Single filtered toolset (native + MCP combined); SDK adds ToolSearchToolset automatically.
    agent: Agent[CoDeps, str | DeferredToolRequests] = Agent(
        model.model,
        deps_type=CoDeps,
        instructions=static_instructions,
        model_settings=model.settings,
        retries=config.tool_retries,
        output_type=[str, DeferredToolRequests],
        history_processors=[
            truncate_tool_results,
            compact_assistant_responses,
            detect_safety_issues,
            inject_opening_context,
            summarize_history_window,
        ],
        toolsets=[tool_registry.toolset],
        capabilities=[CoToolLifecycle()],
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
        entries = load_always_on_memories(ctx.deps.memory_dir)
        if not entries:
            return ""
        max_chars = ctx.deps.config.memory.injection_max_chars
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
    def add_category_awareness_prompt(ctx: RunContext[CoDeps]) -> str:
        """Inject category-level awareness so the model discovers deferred tools via search_tools."""
        from co_cli.context._deferred_tool_prompt import build_category_awareness_prompt

        return build_category_awareness_prompt(ctx.deps.tool_index)

    return agent


async def discover_mcp_tools(
    mcp_toolsets: list, exclude: set[str]
) -> tuple[list[str], dict[str, str], dict[str, ToolInfo]]:
    """Discover MCP tool names by connecting to servers and listing tools.

    Each server self-connects on list_tools() (pydantic-ai lazy init).
    Walks the .wrapped chain recursively to find MCPServer instances
    (handles DeferredLoadingToolset and ApprovalRequiredToolset wrappers).
    Returns (tool_names, errors, mcp_index) where errors maps server prefix to
    the error string for each server where list_tools() failed, and mcp_index maps
    tool name to ToolInfo metadata. Tool names exclude any in ``exclude``.
    MCP tools are deferred by default (visibility=VisibilityPolicyEnum.DEFERRED).
    """
    from pydantic_ai.mcp import MCPServer

    mcp_tool_names: list[str] = []
    errors: dict[str, str] = {}
    mcp_index: dict[str, ToolInfo] = {}

    for toolset in mcp_toolsets:
        # Walk .wrapped chain recursively to find MCPServer
        inner = toolset
        wrapper_count = 0
        while hasattr(inner, "wrapped"):
            inner = inner.wrapped
            wrapper_count += 1
        if not isinstance(inner, MCPServer):
            continue
        prefix = inner.tool_prefix or ""
        try:
            tools = await inner.list_tools()
            for t in tools:
                name = f"{prefix}_{t.name}" if prefix else t.name
                if name not in exclude:
                    mcp_tool_names.append(name)
                    # DeferredLoadingToolset adds 1 wrapper level;
                    # extra levels indicate an approval wrapper
                    approval = wrapper_count > 1
                    mcp_index[name] = ToolInfo(
                        name=name,
                        description=t.description or "",
                        approval=approval,
                        source=ToolSourceEnum.MCP,
                        visibility=VisibilityPolicyEnum.DEFERRED,
                        integration=prefix or None,
                    )
        except Exception as e:
            logger.warning("MCP tool list failed for %r: %s", prefix or "(no prefix)", e)
            errors[prefix] = str(e)

    return sorted(mcp_tool_names), errors, mcp_index
