"""Native toolset construction and approval-resume filter."""

from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from co_cli.config._core import Settings
from co_cli.deps import CoDeps, ToolInfo, VisibilityPolicyEnum
from co_cli.tools.agent_tool import AGENT_TOOL_ATTR
from co_cli.tools.agents import (
    analyze_knowledge,
    reason_about,
    research_web,
)
from co_cli.tools.capabilities import check_capabilities
from co_cli.tools.execute_code import execute_code
from co_cli.tools.files._read import glob, grep, read_file
from co_cli.tools.files._write import patch, write_file
from co_cli.tools.google.calendar import list_calendar_events, search_calendar_events
from co_cli.tools.google.drive import read_drive_file, search_drive_files
from co_cli.tools.google.gmail import create_gmail_draft, list_gmail_emails, search_gmail_emails
from co_cli.tools.knowledge._read import list_knowledge, read_article, search_knowledge
from co_cli.tools.knowledge._write import append_knowledge, save_article, update_knowledge
from co_cli.tools.memory import search_memory
from co_cli.tools.obsidian import list_notes, read_note, search_notes
from co_cli.tools.shell import shell
from co_cli.tools.task_control import (
    task_cancel,
    task_list,
    task_start,
    task_status,
)
from co_cli.tools.todo import todo_read, todo_write
from co_cli.tools.user_input import clarify
from co_cli.tools.web._fetch import web_fetch
from co_cli.tools.web._search import web_search

# Flat explicit list — order is presentation order (no behavioral impact).
NATIVE_TOOLS: tuple[Callable, ...] = (
    # User interaction
    clarify,
    # Introspection & todos
    check_capabilities,
    todo_write,
    todo_read,
    # Knowledge reads
    search_knowledge,
    list_knowledge,
    read_article,
    search_memory,
    # Workspace reads
    glob,
    read_file,
    grep,
    # Web
    web_search,
    web_fetch,
    # Execution
    shell,
    # File writes (deferred)
    write_file,
    patch,
    # Knowledge writes (deferred)
    update_knowledge,
    append_knowledge,
    save_article,
    # Background tasks (deferred)
    task_start,
    task_status,
    task_cancel,
    task_list,
    # Code execution (deferred)
    execute_code,
    # Delegation (deferred)
    research_web,
    analyze_knowledge,
    reason_about,
    # Obsidian (requires obsidian_vault_path)
    list_notes,
    search_notes,
    read_note,
    # Google (requires google_credentials_path)
    search_drive_files,
    read_drive_file,
    list_gmail_emails,
    search_gmail_emails,
    list_calendar_events,
    search_calendar_events,
    create_gmail_draft,
)


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

    Integration tools (obsidian, google) are excluded when the relevant config field
    is absent — they would fail at runtime regardless.

    Returns (native_toolset, native_index) where native_index maps each tool name
    to its ToolInfo metadata.
    """
    toolset: FunctionToolset[CoDeps] = FunctionToolset()
    index: dict[str, ToolInfo] = {}

    for fn in NATIVE_TOOLS:
        info: ToolInfo | None = getattr(fn, AGENT_TOOL_ATTR, None)
        if info is None:
            raise TypeError(
                f"{fn.__module__}.{fn.__name__}: missing @agent_tool(...) decorator. "
                "Every function in NATIVE_TOOLS must declare policy at definition site."
            )
        if info.requires_config is not None and not getattr(config, info.requires_config, None):
            continue
        kwargs: dict[str, Any] = {
            "requires_approval": info.approval,
            "sequential": not info.is_concurrent_safe,
            "defer_loading": info.visibility == VisibilityPolicyEnum.DEFERRED,
        }
        if info.retries is not None:
            kwargs["retries"] = info.retries
        toolset.add_function(fn, **kwargs)
        index[info.name] = info

    return toolset, index
