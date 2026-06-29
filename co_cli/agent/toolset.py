"""Native toolset construction and the per-turn tool-visibility filter."""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

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
from co_cli.tools.system.delegate import delegate  # noqa: F401
from co_cli.tools.system.skills import (  # noqa: F401
    skill_create,
    skill_delete,
    skill_edit,
    skill_patch,
    skill_view,
)
from co_cli.tools.system.tool_view import tool_view  # noqa: F401
from co_cli.tools.system.user_input import clarify  # noqa: F401
from co_cli.tools.tasks.control import (  # noqa: F401
    task_cancel,
    task_close,
    task_list,
    task_start,
    task_status,
    task_write,
)
from co_cli.tools.todo.rw import todo_read, todo_write  # noqa: F401
from co_cli.tools.user_profile.view import user_profile_view  # noqa: F401
from co_cli.tools.user_profile.write import user_profile_write  # noqa: F401
from co_cli.tools.vision.view import image_view  # noqa: F401
from co_cli.tools.web.fetch import web_fetch  # noqa: F401
from co_cli.tools.web.search import web_search  # noqa: F401


def _tool_visibility_filter(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> bool:
    """Per-turn tool visibility gate. Two independent rules, applied every get_tools.

    Deferred gate (every turn): a DEFERRED tool is hidden until the model loads it via
    tool_view — i.e. until its name is in runtime.revealed_tools. This is co's sole
    deferral mechanism (no SDK defer_loading), applied uniformly to native and MCP
    tools; tool_view itself is ALWAYS and so is never gated here.
    """
    entry = ctx.deps.tool_catalog.get(tool_def.name)
    return not (
        entry is not None
        and entry.visibility == VisibilityPolicyEnum.DEFERRED
        and tool_def.name not in ctx.deps.runtime.revealed_tools
    )


def _make_prepare(
    fn: Callable[[CoDeps], bool],
) -> Callable[[RunContext[CoDeps], ToolDefinition], Awaitable[ToolDefinition | None]]:
    """Return a per-turn prepare callback that hides a tool when fn(deps) is False."""

    async def prepare(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> ToolDefinition | None:
        return tool_def if fn(ctx.deps) else None

    return prepare


def _build_native_toolset() -> "tuple[FunctionToolset[CoDeps], dict[str, ToolInfo]]":
    """Build an unfiltered FunctionToolset; deferred visibility is co-owned, not SDK.

    Tools are NOT registered with defer_loading: co hides DEFERRED tools via the
    per-turn _tool_visibility_filter (keyed on tool_catalog visibility + revealed_tools)
    and surfaces them by name through the tool_view tool, so the SDK's keyword loader
    (search_tools) never engages. Visibility lives only in the returned tool_catalog.

    Google tools self-gate per-turn via check_fn=_google_available (wired as a prepare
    hook), so they register here unconditionally and hide each turn until a credential
    exists on disk.

    Returns (native_toolset, native_tool_catalog) where native_tool_catalog maps each tool name
    to its ToolInfo metadata.
    """
    toolset: FunctionToolset[CoDeps] = FunctionToolset()
    catalog: dict[str, ToolInfo] = {}

    for fn in TOOL_REGISTRY:
        info: ToolInfo = getattr(fn, AGENT_TOOL_ATTR)
        kwargs: dict[str, Any] = {
            "requires_approval": info.is_approval_required,
            "sequential": not info.is_concurrent_safe,
        }
        if info.retries is not None:
            kwargs["retries"] = info.retries
        if info.check_fn is not None:
            kwargs["prepare"] = _make_prepare(info.check_fn)
        toolset.add_function(fn, **kwargs)
        catalog[info.name] = info

    return toolset, catalog
