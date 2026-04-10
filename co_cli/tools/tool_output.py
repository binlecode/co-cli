"""Tool result construction using pydantic-ai's ToolReturn separation.

tool_output() returns ToolReturn(return_value=display, metadata=metadata_dict).
pydantic-ai places the display string into ToolReturnPart.content (model sees plain
text) and metadata into ToolReturnPart.metadata (app-side, not sent to LLM).

Usage:
    from co_cli.tools.tool_output import tool_output

    return tool_output("formatted display text", ctx=ctx, count=3)

For call sites without RunContext (helper functions, lifecycle modules):
    from co_cli.tools.tool_output import tool_output_raw

    return tool_output_raw("formatted display text", action="saved")
"""

from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ToolReturn

from co_cli.tools.tool_result_storage import TOOL_RESULT_MAX_SIZE, persist_if_oversized

if TYPE_CHECKING:
    from pydantic_ai import RunContext

    from co_cli.deps import CoDeps


def tool_output(
    display: str,
    *,
    ctx: "RunContext[CoDeps]",
    **metadata: Any,
) -> ToolReturn:
    """Construct a ToolReturn with display as return_value and extras as metadata."""
    tool_name = ctx.tool_name or ""
    info = ctx.deps.tool_index.get(tool_name)
    threshold = info.max_result_size if info else TOOL_RESULT_MAX_SIZE
    if len(display) > threshold:
        display = persist_if_oversized(
            display,
            ctx.deps.tool_results_dir,
            tool_name,
            max_size=threshold,
        )
    return ToolReturn(return_value=display, metadata=metadata or None)


def tool_output_raw(
    display: str,
    **metadata: Any,
) -> ToolReturn:
    """Construct a ToolReturn without RunContext — no size checking.

    Use only in helper functions that lack RunContext (e.g. memory lifecycle,
    memory save). Tool functions with ctx should always use tool_output().
    """
    return ToolReturn(return_value=display, metadata=metadata or None)


# Shared type alias for Frontend.on_tool_complete, _run_stream_segment dispatch,
# and TerminalFrontend._render_tool_panel — one edit point if a new result type is added.
ToolResultPayload = str | ToolReturn | None
