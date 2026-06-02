"""Native toolset construction and approval-resume filter."""

from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from co_cli.config.core import Settings
from co_cli.deps import CoDeps, ToolInfo, VisibilityPolicyEnum
from co_cli.tools.agent_tool import AGENT_TOOL_ATTR, TOOL_REGISTRY

# Import all tool modules to trigger @agent_tool self-registration into TOOL_REGISTRY.
from co_cli.tools.files.read import file_read, file_search  # noqa: F401
from co_cli.tools.files.write import file_patch, file_write  # noqa: F401
from co_cli.tools.google.calendar import google_calendar_list, google_calendar_search  # noqa: F401
from co_cli.tools.google.drive import google_drive_read, google_drive_search  # noqa: F401
from co_cli.tools.google.gmail import (  # noqa: F401
    google_gmail_draft,
    google_gmail_list,
    google_gmail_search,
)
from co_cli.tools.memory.manage import (  # noqa: F401
    memory_append,
    memory_create,
    memory_delete,
    memory_replace,
)
from co_cli.tools.memory.recall import memory_search  # noqa: F401
from co_cli.tools.memory.view import memory_view  # noqa: F401
from co_cli.tools.session.recall import session_search  # noqa: F401
from co_cli.tools.session.view import session_view  # noqa: F401
from co_cli.tools.shell.execute import shell_exec  # noqa: F401
from co_cli.tools.system.capabilities import capabilities_check  # noqa: F401
from co_cli.tools.system.skills import (  # noqa: F401
    skill_create,
    skill_delete,
    skill_edit,
    skill_patch,
    skill_view,
)
from co_cli.tools.system.user_input import clarify  # noqa: F401
from co_cli.tools.tasks.control import (  # noqa: F401
    task_cancel,
    task_list,
    task_start,
    task_status,
)
from co_cli.tools.todo.rw import todo_read, todo_write  # noqa: F401
from co_cli.tools.web.fetch import web_fetch  # noqa: F401
from co_cli.tools.web.search import web_search  # noqa: F401


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
    return entry is None or entry.visibility == VisibilityPolicyEnum.ALWAYS


def _config_requirement_met(info: ToolInfo, config: Settings) -> bool:
    return info.requires_config is None or bool(getattr(config, info.requires_config, None))


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

    A tool whose requires_config names an absent config field is excluded; no tool
    currently sets one. Google tools self-gate per-turn via check_fn=_google_available
    (wired as a prepare hook), so they register here unconditionally and hide each turn
    until a credential exists on disk.

    Returns (native_toolset, native_index) where native_index maps each tool name
    to its ToolInfo metadata.
    """
    toolset: FunctionToolset[CoDeps] = FunctionToolset()
    index: dict[str, ToolInfo] = {}

    for fn in TOOL_REGISTRY:
        info: ToolInfo = getattr(fn, AGENT_TOOL_ATTR)
        if not _config_requirement_met(info, config):
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
