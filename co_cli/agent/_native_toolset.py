"""Native toolset construction and approval-resume filter."""

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from co_cli.config._core import Settings
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.tools.agents import (
    delegate_analyst,
    delegate_coder,
    delegate_reasoner,
    delegate_researcher,
)
from co_cli.tools.articles import read_article, save_article, search_articles, search_knowledge
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.files import edit_file, find_in_files, list_directory, read_file, write_file
from co_cli.tools.google_calendar import list_calendar_events, search_calendar_events
from co_cli.tools.google_drive import read_drive_file, search_drive_files
from co_cli.tools.google_gmail import create_gmail_draft, list_gmail_emails, search_gmail_emails
from co_cli.tools.memory import list_memories, search_memories
from co_cli.tools.obsidian import list_notes, read_note, search_notes
from co_cli.tools.session_search import session_search
from co_cli.tools.shell import run_shell_command
from co_cli.tools.task_control import (
    cancel_background_task,
    check_task_status,
    list_background_tasks,
    start_background_task,
)
from co_cli.tools.todo import read_todos, write_todos
from co_cli.tools.web import web_fetch, web_search


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
        sequential: bool = False,
        visibility: VisibilityPolicyEnum,
        integration: str | None = None,
        retries: int | None = None,
        max_result_size: int = 50_000,
    ) -> None:
        name = fn.__name__
        description = fn.__doc__.split("\n")[0].strip() if fn.__doc__ else fn.__name__
        kwargs: dict[str, Any] = {
            "requires_approval": approval,
            "sequential": sequential,
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
    _register_tool(
        write_file, approval=True, sequential=True, visibility=_deferred_visible, retries=1
    )
    _register_tool(
        edit_file, approval=True, sequential=True, visibility=_deferred_visible, retries=1
    )

    # Knowledge write tools
    _register_tool(save_article, approval=True, visibility=_deferred_visible, retries=1)

    # Background task tools
    _register_tool(start_background_task, approval=True, visibility=_deferred_visible)
    _register_tool(check_task_status, visibility=_deferred_visible)
    _register_tool(cancel_background_task, visibility=_deferred_visible)
    _register_tool(list_background_tasks, visibility=_deferred_visible)

    # Delegation tools
    _register_tool(delegate_coder, visibility=_deferred_visible)
    _register_tool(delegate_researcher, visibility=_deferred_visible)
    _register_tool(delegate_analyst, visibility=_deferred_visible)
    _register_tool(delegate_reasoner, visibility=_deferred_visible)

    # Session history search
    _register_tool(session_search, visibility=_deferred_visible)

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
