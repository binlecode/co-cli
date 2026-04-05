"""Typed tool result payload for the tool lifecycle contract.

ToolResult is a TypedDict with a _kind discriminator. pydantic-ai serializes
tool returns to dict before _run_stream_segment() sees them, so isinstance(content,
ToolResult) would never be True. The _kind discriminator is the only reliable
detection mechanism.

Usage:
    from co_cli.tools.tool_output import tool_output

    return tool_output("formatted display text", count=3)
"""

from typing import Any, Literal, Required, TypedDict, TYPE_CHECKING

from co_cli.context._tool_result_storage import persist_if_oversized, TOOL_RESULT_MAX_SIZE

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from co_cli.deps import CoDeps


class ToolResult(TypedDict, total=False):
    """Typed completion payload for the tool lifecycle contract.

    _kind must always be "tool_result". display is the rendered panel content.
    Additional metadata fields are allowed via **metadata in tool_output().
    """

    _kind: Required[Literal["tool_result"]]
    display: str


def tool_output(
    display: str,
    *,
    ctx: "RunContext[CoDeps] | None" = None,
    **metadata: Any,
) -> ToolResult:
    """Construct a ToolResult payload with the required _kind discriminator."""
    if ctx is not None and len(display) > TOOL_RESULT_MAX_SIZE:
        display = persist_if_oversized(
            display, ctx.deps.config.tool_results_dir, ctx.tool_name,
        )
    return ToolResult(_kind="tool_result", display=display, **metadata)  # type: ignore[misc]


# Shared type alias for Frontend.on_tool_complete, _run_stream_segment dispatch,
# and TerminalFrontend._render_tool_panel — one edit point if a new result type is added.
ToolResultPayload = str | ToolResult | None
