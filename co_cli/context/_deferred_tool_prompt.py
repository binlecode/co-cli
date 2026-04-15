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
    "start_background_task": "background tasks",
    "check_task_status": "background tasks",
    "cancel_background_task": "background tasks",
    "list_background_tasks": "background tasks",
    "delegate_coder": "sub-agents",
    "delegate_researcher": "sub-agents",
    "delegate_analyst": "sub-agents",
    "delegate_reasoner": "sub-agents",
}

# Representative tool names for each native category (for keyword-formation hints)
_NATIVE_CATEGORY_REPS: dict[str, list[str]] = {
    "file editing": ["write_file", "patch"],
    "background tasks": ["start_background_task"],
    "memory management": ["save_article"],
    "sub-agents": [
        "delegate_coder",
        "delegate_researcher",
        "delegate_analyst",
        "delegate_reasoner",
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
