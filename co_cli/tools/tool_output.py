"""Tool result construction using pydantic-ai's ToolReturn separation.

tool_output() returns ToolReturn(return_value=display, metadata=metadata_dict).
pydantic-ai places the display string into ToolReturnPart.content (model sees plain
text) and metadata into ToolReturnPart.metadata (app-side, not sent to LLM).

Usage:
    from co_cli.tools.tool_output import tool_output

    return tool_output("formatted display text", count=3)
"""

from typing import Any, TYPE_CHECKING

from pydantic_ai.messages import ToolReturn

from co_cli.tools.tool_result_storage import persist_if_oversized, TOOL_RESULT_MAX_SIZE

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from co_cli.deps import CoDeps


def tool_output(
    display: str,
    *,
    ctx: "RunContext[CoDeps] | None" = None,
    **metadata: Any,
) -> ToolReturn:
    """Construct a ToolReturn with display as return_value and extras as metadata."""
    if ctx is not None:
        info = ctx.deps.tool_index.get(ctx.tool_name)
        threshold = info.max_result_size if info else TOOL_RESULT_MAX_SIZE
        if len(display) > threshold:
            display = persist_if_oversized(
                display, ctx.deps.config.tool_results_dir, ctx.tool_name,
                max_size=threshold,
            )
    return ToolReturn(return_value=display, metadata=metadata or None)


# Shared type alias for Frontend.on_tool_complete, _run_stream_segment dispatch,
# and TerminalFrontend._render_tool_panel — one edit point if a new result type is added.
ToolResultPayload = str | ToolReturn | None
