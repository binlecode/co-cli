"""Build a tool-category-level awareness prompt for deferred tool discovery.

The SDK's ToolSearchToolset handles per-tool deferred visibility. This module
provides a tool-category-level prompt so the model knows which tool-category
domains are available via search_tools. Native tool categories include
representative tool names (e.g. "background tasks (task_start)") to reduce
keyword-formation burden for local models; integration tool categories are
listed by label only.
"""

from co_cli.deps import ToolInfo, VisibilityPolicyEnum

# Native deferred tool → tool-category label
_NATIVE_TOOL_CATEGORIES: dict[str, str] = {
    "task_start": "background tasks",
    "task_status": "background tasks",
    "task_cancel": "background tasks",
    "task_list": "background tasks",
    "code_execute": "code execution",
    "web_research": "sub-agents",
    "knowledge_analyze": "sub-agents",
    "reason": "sub-agents",
}

# Representative tool names for each native tool category (keyword-formation hints)
_NATIVE_TOOL_CATEGORY_REPS: dict[str, list[str]] = {
    "background tasks": ["task_start"],
    "code execution": ["code_execute"],
    "sub-agents": [
        "web_research",
        "knowledge_analyze",
        "reason",
    ],
}

# Integration field → display tool-category label
_INTEGRATION_TOOL_CATEGORIES: dict[str, str] = {
    "obsidian": "Obsidian notes",
    "google_gmail": "Gmail",
    "google_calendar": "Google Calendar",
    "google_drive": "Google Drive",
}


def build_tool_category_awareness_prompt(
    tool_index: dict[str, ToolInfo],
) -> str:
    """Return a tool-category-level prompt listing available deferred tool categories.

    Native tool categories include representative tool names to reduce keyword-formation
    burden for local models. Config-gated tools only appear when their integration
    is registered in tool_index. MCP tools use their integration (server prefix)
    as the tool-category name. Returns empty string when no deferred tools exist.
    """
    native_tool_categories: set[str] = set()
    integration_tool_categories: set[str] = set()
    for info in tool_index.values():
        if info.visibility != VisibilityPolicyEnum.DEFERRED:
            continue
        if info.integration and info.integration in _INTEGRATION_TOOL_CATEGORIES:
            integration_tool_categories.add(_INTEGRATION_TOOL_CATEGORIES[info.integration])
        elif info.name in _NATIVE_TOOL_CATEGORIES:
            native_tool_categories.add(_NATIVE_TOOL_CATEGORIES[info.name])
        elif info.integration:
            # MCP/domain tools: use integration name as tool category
            integration_tool_categories.add(info.integration)
    if not native_tool_categories and not integration_tool_categories:
        return ""
    parts: list[str] = []
    for cat in sorted(native_tool_categories):
        reps = _NATIVE_TOOL_CATEGORY_REPS.get(cat)
        if reps:
            parts.append(f"{cat} ({', '.join(reps)})")
        else:
            parts.append(cat)
    parts.extend(sorted(integration_tool_categories))
    return f"Additional capabilities available via search_tools: {', '.join(parts)}."
