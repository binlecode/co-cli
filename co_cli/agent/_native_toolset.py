"""Native toolset construction and approval-resume filter."""

from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from co_cli.config.core import Settings
from co_cli.deps import CoDeps, ToolInfo, VisibilityPolicyEnum
from co_cli.tools.agent_tool import AGENT_TOOL_ATTR
from co_cli.tools.agents import (
    knowledge_analyze,
    reason,
    web_research,
)
from co_cli.tools.capabilities import capabilities_check
from co_cli.tools.execute_code import code_execute
from co_cli.tools.files.read import file_find, file_read, file_search
from co_cli.tools.files.write import file_patch, file_write
from co_cli.tools.google.calendar import google_calendar_list, google_calendar_search
from co_cli.tools.google.drive import google_drive_read, google_drive_search
from co_cli.tools.google.gmail import google_gmail_draft, google_gmail_list, google_gmail_search
from co_cli.tools.memory.read import memory_list, memory_read
from co_cli.tools.memory.recall import memory_search
from co_cli.tools.memory.write import (
    memory_create,
    memory_modify,
)
from co_cli.tools.obsidian import obsidian_list, obsidian_read, obsidian_search
from co_cli.tools.shell import shell
from co_cli.tools.task_control import (
    task_cancel,
    task_list,
    task_start,
    task_status,
)
from co_cli.tools.todo import todo_read, todo_write
from co_cli.tools.user_input import clarify
from co_cli.tools.web.fetch import web_fetch
from co_cli.tools.web.search import web_search

# Flat explicit list — order is presentation order (no behavioral impact).
NATIVE_TOOLS: tuple[Callable, ...] = (
    # User interaction
    clarify,
    # Introspection & todos
    capabilities_check,
    todo_write,
    todo_read,
    # Knowledge reads
    memory_list,
    memory_read,
    memory_search,
    # Workspace reads
    file_find,
    file_read,
    file_search,
    # Web
    web_search,
    web_fetch,
    # Execution
    shell,
    # File writes (deferred)
    file_write,
    file_patch,
    # Knowledge writes (deferred)
    memory_create,
    memory_modify,
    # Background tasks (deferred)
    task_start,
    task_status,
    task_cancel,
    task_list,
    # Code execution (deferred)
    code_execute,
    # Delegation (deferred)
    web_research,
    knowledge_analyze,
    reason,
    # Obsidian (requires obsidian_vault_path)
    obsidian_list,
    obsidian_search,
    obsidian_read,
    # Google (requires google_credentials_path)
    google_drive_search,
    google_drive_read,
    google_gmail_list,
    google_gmail_search,
    google_calendar_list,
    google_calendar_search,
    google_gmail_draft,
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


def _make_prepare(fn: Callable[[CoDeps], bool]):
    """Return a per-turn prepare callback that hides a tool when fn(deps) is False."""

    async def prepare(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
        return tool_def if fn(ctx.deps) else None

    return prepare


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
        if info.check_fn is not None:
            kwargs["prepare"] = _make_prepare(info.check_fn)
        toolset.add_function(fn, **kwargs)
        index[info.name] = info

    return toolset, index
