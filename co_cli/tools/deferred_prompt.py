"""Build a per-tool awareness prompt for deferred tool discovery.

The SDK's ToolSearchToolset handles per-tool deferred visibility, but a deferred
tool's full schema is absent from the prompt until loaded via search_tools. This
module emits a per-tool stub (name + one-line purpose) for every DEFERRED tool so
the model knows the tool exists and what it does, and can load it via search_tools
before calling it. The list is derived from the live tool_index — complete by
construction, with no hardcoded allowlist to forget when a tool goes DEFERRED.
"""

from co_cli.deps import ToolInfo, VisibilityPolicyEnum

# Max chars for a stub one-liner; longer descriptions are truncated with an ellipsis.
_ONE_LINER_MAX_CHARS = 100


def _stub_one_liner(description: str) -> str:
    """Return the first non-empty line of description, stripped and length-capped.

    Truncates to _ONE_LINER_MAX_CHARS (ellipsis included) so a stub never re-imports
    the full multi-line schema cost the DEFERRED flip removed. Returns "" when the
    description has no non-empty line (caller emits a name-only stub).
    """
    for raw_line in description.splitlines():
        line = raw_line.strip()
        if line:
            if len(line) > _ONE_LINER_MAX_CHARS:
                return line[: _ONE_LINER_MAX_CHARS - 1] + "…"
            return line
    return ""


def build_deferred_tool_awareness_prompt(
    tool_index: dict[str, ToolInfo],
) -> str:
    """Return a per-tool stub prompt for every DEFERRED tool in tool_index.

    Each deferred tool emits one line: ``- `name`: one-liner`` (or ``- `name``` when
    the description is empty). Config-gated and MCP tools only appear when their
    integration is registered in tool_index, so gating falls out naturally. Returns
    the empty string when no deferred tools exist — the per-turn instruction slot
    relies on that contract.
    """
    lines: list[str] = []
    for name in sorted(tool_index):
        info = tool_index[name]
        if info.visibility != VisibilityPolicyEnum.DEFERRED:
            continue
        one_liner = _stub_one_liner(info.description)
        if one_liner:
            lines.append(f"- `{name}`: {one_liner}")
        else:
            lines.append(f"- `{name}`")
    if not lines:
        return ""
    header = (
        "Additional tools are available but not loaded. "
        "Load a tool with search_tools before calling it:"
    )
    return header + "\n" + "\n".join(lines)
