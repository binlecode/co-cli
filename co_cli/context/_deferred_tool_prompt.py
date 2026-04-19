"""Build a category-level awareness prompt for deferred tool discovery.

The SDK's ToolSearchToolset handles per-tool deferred visibility. This module
provides a category-level prompt so the model knows which capability domains
are available via search_tools. Native categories include representative tool
names (e.g. "file editing (write_file, patch)") to reduce keyword-formation
burden for local models; integration categories are listed by label only.
"""

from co_cli.deps import ToolInfo, VisibilityPolicyEnum

# Native deferred tool → category label
_NATIVE_CATEGORIES: dict[str, str] = {
    "write_file": "file editing",
    "patch": "file editing",
    "save_article": "memory management",
    "task_start": "background tasks",
    "task_status": "background tasks",
    "task_cancel": "background tasks",
    "task_list": "background tasks",
    "execute_code": "code execution",
    "research_web": "sub-agents",
    "analyze_knowledge": "sub-agents",
    "reason_about": "sub-agents",
}

# Representative tool names for each native category (for keyword-formation hints)
_NATIVE_CATEGORY_REPS: dict[str, list[str]] = {
    "file editing": ["write_file", "patch"],
    "background tasks": ["task_start"],
    "memory management": ["save_article"],
    "code execution": ["execute_code"],
    "sub-agents": [
        "research_web",
        "analyze_knowledge",
        "reason_about",
    ],
}

# Integration field → display label
_INTEGRATION_CATEGORIES: dict[str, str] = {
    "obsidian": "Obsidian notes",
    "google_gmail": "Gmail",
    "google_calendar": "Google Calendar",
    "google_drive": "Google Drive",
}


def build_category_awareness_prompt(
    tool_index: dict[str, ToolInfo],
) -> str:
    """Return a category-level prompt listing available deferred tool categories.

    Native categories include representative tool names to reduce keyword-formation
    burden for local models. Config-gated tools only appear when their integration
    is registered in tool_index. MCP tools use their integration (server prefix)
    as the category name. Returns empty string when no deferred tools exist.
    """
    native_categories: set[str] = set()
    integration_categories: set[str] = set()
    for info in tool_index.values():
        if info.visibility != VisibilityPolicyEnum.DEFERRED:
            continue
        if info.integration and info.integration in _INTEGRATION_CATEGORIES:
            integration_categories.add(_INTEGRATION_CATEGORIES[info.integration])
        elif info.name in _NATIVE_CATEGORIES:
            native_categories.add(_NATIVE_CATEGORIES[info.name])
        elif info.integration:
            # MCP/domain tools: use integration name as category
            integration_categories.add(info.integration)
    if not native_categories and not integration_categories:
        return ""
    parts: list[str] = []
    for cat in sorted(native_categories):
        reps = _NATIVE_CATEGORY_REPS.get(cat)
        if reps:
            parts.append(f"{cat} ({', '.join(reps)})")
        else:
            parts.append(cat)
    parts.extend(sorted(integration_categories))
    return f"Additional capabilities available via search_tools: {', '.join(parts)}."
