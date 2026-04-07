"""Build the deferred-tool prompt fragment for model awareness."""

from co_cli.deps import ToolInfo, LoadPolicy


def build_deferred_tool_prompt(
    tool_index: dict[str, ToolInfo],
    discovered_tools: set[str],
) -> str | None:
    """Return a prompt fragment listing undiscovered deferred tools, or None if empty.

    Pure function: reads tool_index and discovered_tools, returns formatted text.
    """
    undiscovered = [
        tc for tc in tool_index.values()
        if tc.load == LoadPolicy.DEFERRED and tc.name not in discovered_tools
    ]
    if not undiscovered:
        return None

    lines = [
        "Additional tools are available but not yet loaded. "
        "Call search_tools(query) to discover and unlock them.",
        "",
        "Deferred tools:",
    ]
    for tc in sorted(undiscovered, key=lambda t: t.name):
        parts = [f"  - {tc.name}: {tc.description}"]
        if tc.integration:
            parts.append(f" ({tc.integration})")
        if tc.search_hint:
            parts.append(f" [hints: {tc.search_hint}]")
        lines.append("".join(parts))

    return "\n".join(lines)
