"""Native toolset construction, the per-turn tool-visibility filter, and the call-seam call_tool wrapper."""

import json
from collections.abc import Callable
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

from co_cli.config.tuning import MAX_TOOL_CALLS_PER_MODEL_REQUEST, SPILL_THRESHOLD_CHARS
from co_cli.deps import CoDeps, ToolInfo, ToolSourceEnum, VisibilityPolicyEnum
from co_cli.fileio.spill import spill_with_span
from co_cli.observability.serialize import serialize_tool_args, truncate_tool_result
from co_cli.observability.tracing import current_span, pop_span, push_span
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
from co_cli.tools.tool_call_limit import make_exceeded_payload
from co_cli.tools.vision.view import image_view  # noqa: F401
from co_cli.tools.web.fetch import web_fetch  # noqa: F401
from co_cli.tools.web.search import web_search  # noqa: F401


def _tool_visibility_filter(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> bool:
    """Per-turn tool visibility gate. Two independent rules, applied every get_tools.

    Deferred gate (every turn): a DEFERRED tool is hidden until the model loads it via
    tool_view — i.e. until its name is in runtime.revealed_tools. This is co's sole
    deferral mechanism (no SDK defer_loading), applied uniformly to native and MCP
    tools; tool_view itself is ALWAYS and so is never gated here.

    Resume gate (approval-resume turns only): narrow to approved tools + always-visible
    tools so the resumed run re-presents only what the pending approval needs.
    """
    entry = ctx.deps.tool_catalog.get(tool_def.name)
    if (
        entry is not None
        and entry.visibility == VisibilityPolicyEnum.DEFERRED
        and tool_def.name not in ctx.deps.runtime.revealed_tools
    ):
        return False
    resume = ctx.deps.runtime.resume_tool_names
    if resume is None:
        return True
    if tool_def.name in resume:
        return True
    return entry is None or entry.visibility == VisibilityPolicyEnum.ALWAYS


def _make_prepare(fn: Callable[[CoDeps], bool]):
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


class _CallSeamToolset(WrapperToolset[CoDeps]):
    """Single explicit seam at the routing ``call_tool`` boundary.

    Co-locates the three concerns that can only live at the per-call boundary, as
    straight-line ordered code (no LIFO invariant, no cross-component global-span
    bridge): the ``tool`` span with ``co.tool.*`` attributes, the per-model-request
    tool-call cap, and MCP-result spill. ``ctx.run_step`` and ``ctx.deps`` are
    available here. This replaces the per-tool hooks of the former pydantic-ai
    lifecycle middleware — span, cap, and spill, co-located as linear code.
    """

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[CoDeps],
        tool: ToolsetTool[CoDeps],
    ) -> Any:
        runtime = ctx.deps.runtime
        cap = MAX_TOOL_CALLS_PER_MODEL_REQUEST

        # Per-model-request cap accounting. One ctx.run_step == one model request;
        # all tools of one assistant message share it. Immediate increment of the
        # consecutive-violation streak at the (cap+1)-th call, delayed reset on the
        # next request when the prior request stayed within the cap. The orchestrator
        # finalizes the last request's reset at the run boundary.
        if ctx.run_step != runtime.tool_call_limit_run_step:
            if (
                runtime.tool_call_limit_run_step != -1
                and runtime.tool_calls_in_model_request <= cap
            ):
                runtime.consecutive_tool_cap_violations = 0
            runtime.tool_call_limit_run_step = ctx.run_step
            runtime.tool_calls_in_model_request = 0
        runtime.tool_calls_in_model_request += 1
        if runtime.tool_calls_in_model_request == cap + 1:
            runtime.consecutive_tool_cap_violations += 1

        push_span(
            f"tool {name}",
            kind="tool",
            attributes={
                "co.tool.name": name,
                "co.tool.args": serialize_tool_args(tool_args),
            },
        )
        try:
            args_chars = len(json.dumps(tool_args, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            args_chars = 0
        current_span().set_attribute("co.tool.args_chars", args_chars)

        info = ctx.deps.tool_catalog.get(name)
        try:
            if runtime.tool_calls_in_model_request > cap:
                result: Any = json.dumps(
                    make_exceeded_payload(runtime.tool_calls_in_model_request)
                )
            else:
                result = await super().call_tool(name, tool_args, ctx, tool)
                # MCP results are plain strings that bypass tool_output() — spill here.
                if isinstance(result, str) and info and info.source == ToolSourceEnum.MCP:
                    threshold = (
                        info.spill_threshold_chars
                        if info.spill_threshold_chars is not None
                        else SPILL_THRESHOLD_CHARS
                    )
                    result = spill_with_span(
                        result,
                        tool_name=name,
                        tool_results_dir=ctx.deps.tool_results_dir,
                        threshold_chars=threshold,
                    )
        except Exception as exc:
            pop_span(status="ERROR", status_msg=str(exc))
            raise

        span = current_span()
        span.set_attribute("co.tool.result", truncate_tool_result(result))
        span.set_attribute("co.tool.result_size", len(str(result)))
        if info:
            span.set_attribute("co.tool.source", info.source.value)
            span.set_attribute("co.tool.requires_approval", info.is_approval_required)
        pop_span()
        return result
