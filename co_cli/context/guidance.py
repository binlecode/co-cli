"""Static guidance constants for toolset-availability gated prompt assembly.

Content that was previously unconditional in rule files is moved here and
emitted only when the matching tool is present in the session tool_index.
This keeps rule files tool-agnostic while preserving guidance accuracy.
"""

from __future__ import annotations

from co_cli.deps import ToolInfo

MEMORY_GUIDANCE = """\
## Memory
Character base memories and user experience memories are both loaded in the
system prompt before the first turn — do not call memory_search at turn start.
Use memory_search mid-conversation to look up specific facts relevant to the current task.
When the user references something from a past conversation or session, call
memory_search before asking them to repeat themselves — it covers both persistent
knowledge artifacts and past session transcripts in one call.

If memory_search returns no results, make at most one broader retry when a
clear broader query exists. After that, surface the miss explicitly instead of
continuing to search variations."""

CAPABILITIES_GUIDANCE = """\
## Capability self-check
When the user asks what capabilities you have, whether you can use a specific
tool or integration, or why an expected capability is unavailable or degraded,
call `capabilities_check` before answering. It reports the current tool surface,
approval-gated actions, unavailable or limited components, and active fallbacks.
Pair it with `search_tools` when the question is also about deferred tools."""


def build_toolset_guidance(tool_index: dict[str, ToolInfo]) -> str:
    """Emit tool-specific guidance for tools actually present in the session."""
    parts: list[str] = []
    tool_names = set(tool_index.keys())

    if "memory_search" in tool_names:
        parts.append(MEMORY_GUIDANCE)
    if "capabilities_check" in tool_names:
        parts.append(CAPABILITIES_GUIDANCE)

    return "\n\n".join(parts)
