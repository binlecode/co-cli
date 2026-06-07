"""Static guidance constants for toolset-availability gated prompt assembly.

Content that was previously unconditional in rule files is moved here and
emitted only when the matching tool is present in the session tool_index.
This keeps rule files tool-agnostic while preserving guidance accuracy.
"""

from __future__ import annotations

from co_cli.deps import ToolInfo

CAPABILITIES_GUIDANCE = """\
## Capability self-check
When the user asks what capabilities you have, whether you can use a specific
tool or integration, or why an expected capability is unavailable or degraded,
call `capabilities_check` before answering. It reports the current tool surface,
approval-gated actions, unavailable or limited components, and active fallbacks.
Pair it with `tool_view` when the question is also about deferred tools."""


def build_toolset_guidance(tool_index: dict[str, ToolInfo]) -> str:
    """Emit tool-specific guidance for tools actually present in the session."""
    parts: list[str] = []
    tool_names = set(tool_index.keys())

    if "capabilities_check" in tool_names:
        parts.append(CAPABILITIES_GUIDANCE)

    return "\n\n".join(parts)
