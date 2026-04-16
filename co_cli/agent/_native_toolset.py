"""Native toolset construction and approval-resume filter."""

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from co_cli.config._core import Settings
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.tools.agents import (
    analyze_knowledge,
    reason_about,
    research_web,
)
from co_cli.tools.articles import read_article, save_article, search_articles, search_knowledge
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.execute_code import execute_code
from co_cli.tools.files import glob, grep, patch, read_file, write_file
from co_cli.tools.google.calendar import list_calendar_events, search_calendar_events
from co_cli.tools.google.drive import read_drive_file, search_drive_files
from co_cli.tools.google.gmail import create_gmail_draft, list_gmail_emails, search_gmail_emails
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

    Normal turns: pass all tools — SDK ToolSearchToolset handles
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

    Integration tools (obsidian, google) are excluded when the relevant
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
        is_read_only: bool = False,
        is_concurrent_safe: bool = False,
        visibility: VisibilityPolicyEnum,
        integration: str | None = None,
        retries: int | None = None,
        max_result_size: int = 50_000,
    ) -> None:
        assert not (is_read_only and not is_concurrent_safe), (
            f"{fn.__name__}: is_read_only=True requires is_concurrent_safe=True"
        )
        name = fn.__name__
        description = fn.__doc__.split("\n")[0].strip() if fn.__doc__ else fn.__name__
        tool_info = ToolInfo(
            name=name,
            description=description,
            approval=approval,
            source=ToolSourceEnum.NATIVE,
            visibility=visibility,
            integration=integration,
            max_result_size=max_result_size,
            is_read_only=is_read_only,
            is_concurrent_safe=is_concurrent_safe,
            retries=retries,
        )
        kwargs: dict[str, Any] = {
            "requires_approval": tool_info.approval,
            "sequential": not tool_info.is_concurrent_safe,
            "defer_loading": tool_info.visibility == VisibilityPolicyEnum.DEFERRED,
        }
        if tool_info.retries is not None:
            kwargs["retries"] = tool_info.retries
        native_toolset.add_function(fn, **kwargs)
        native_index[name] = tool_info

    # --- Always-visible tools (defer_loading=False) ---
    _always_visible = VisibilityPolicyEnum.ALWAYS

    # Capability introspection
    _register_tool(
        check_capabilities, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible
    )

    # Session task tracking
    _register_tool(write_todos, is_concurrent_safe=True, visibility=_always_visible)
    _register_tool(
        read_todos, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible
    )

    # Knowledge reads
    _register_tool(
        search_memories, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible
    )
    _register_tool(
        search_knowledge, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible
    )
    _register_tool(
        search_articles, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible
    )
    _register_tool(
        read_article, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible
    )
    _register_tool(
        list_memories, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible
    )

    # Workspace reads
    _register_tool(glob, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible)
    _register_tool(
        read_file,
        is_read_only=True,
        is_concurrent_safe=True,
        visibility=_always_visible,
        max_result_size=80_000,
    )
    _register_tool(grep, is_read_only=True, is_concurrent_safe=True, visibility=_always_visible)

    # Web
    _register_tool(
        web_search,
        is_read_only=True,
        is_concurrent_safe=True,
        visibility=_always_visible,
        retries=3,
    )
    _register_tool(
        web_fetch,
        is_read_only=True,
        is_concurrent_safe=True,
        visibility=_always_visible,
        retries=3,
    )

    # Execution
    _register_tool(
        run_shell_command,
        is_concurrent_safe=True,
        visibility=_always_visible,
        max_result_size=30_000,
    )

    # --- Deferred tools (defer_loading=True, discovered via SDK search_tools) ---
    _deferred_visible = VisibilityPolicyEnum.DEFERRED

    # File write tools
    _register_tool(write_file, approval=True, visibility=_deferred_visible, retries=1)
    _register_tool(patch, approval=True, visibility=_deferred_visible, retries=1)

    # Knowledge write tools
    _register_tool(
        save_article,
        approval=True,
        is_concurrent_safe=True,
        visibility=_deferred_visible,
        retries=1,
    )

    # Background task tools
    _register_tool(
        start_background_task, approval=True, is_concurrent_safe=True, visibility=_deferred_visible
    )
    _register_tool(
        check_task_status, is_read_only=True, is_concurrent_safe=True, visibility=_deferred_visible
    )
    _register_tool(cancel_background_task, is_concurrent_safe=True, visibility=_deferred_visible)
    _register_tool(
        list_background_tasks,
        is_read_only=True,
        is_concurrent_safe=True,
        visibility=_deferred_visible,
    )

    # Code execution
    _register_tool(execute_code, is_concurrent_safe=False, visibility=_deferred_visible)

    # Delegation tools
    _register_tool(research_web, is_concurrent_safe=True, visibility=_deferred_visible)
    _register_tool(analyze_knowledge, is_concurrent_safe=True, visibility=_deferred_visible)
    _register_tool(reason_about, is_concurrent_safe=True, visibility=_deferred_visible)

    # Session history search
    _register_tool(
        session_search, is_read_only=True, is_concurrent_safe=True, visibility=_deferred_visible
    )

    # Integration tools — excluded when the required config/credential is absent
    if config.obsidian_vault_path:
        _register_tool(
            list_notes,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="obsidian",
        )
        _register_tool(
            search_notes,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="obsidian",
        )
        _register_tool(
            read_note,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="obsidian",
        )

    if config.google_credentials_path:
        _register_tool(
            search_drive_files,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="google_drive",
            retries=3,
        )
        _register_tool(
            read_drive_file,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="google_drive",
            retries=3,
        )
        _register_tool(
            list_gmail_emails,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="google_gmail",
            retries=3,
        )
        _register_tool(
            search_gmail_emails,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="google_gmail",
            retries=3,
        )
        _register_tool(
            list_calendar_events,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="google_calendar",
            retries=3,
        )
        _register_tool(
            search_calendar_events,
            is_read_only=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="google_calendar",
            retries=3,
        )
        _register_tool(
            create_gmail_draft,
            approval=True,
            is_concurrent_safe=True,
            visibility=_deferred_visible,
            integration="google_gmail",
            retries=1,
        )

    return native_toolset, native_index
