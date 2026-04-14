"""Build a category-level awareness prompt for deferred tool discovery.

The SDK's ToolSearchToolset handles per-tool deferred visibility. This module
provides a lightweight category-level prompt (~100 tokens) so the model knows
which capability domains are available via search_tools without listing every
individual tool name.
"""

from co_cli.deps import ToolInfo, VisibilityPolicyEnum

# Native deferred tool → category label
_NATIVE_CATEGORIES: dict[str, str] = {
    "write_file": "file editing",
    "edit_file": "file editing",
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

    Config-gated tools only appear when their integration is registered in tool_index.
    MCP tools use their integration (server prefix) as the category name.
    Returns empty string when no deferred tools exist.
    """
    categories: set[str] = set()
    for info in tool_index.values():
        if info.visibility != VisibilityPolicyEnum.DEFERRED:
            continue
        if info.integration and info.integration in _INTEGRATION_CATEGORIES:
            categories.add(_INTEGRATION_CATEGORIES[info.integration])
        elif info.name in _NATIVE_CATEGORIES:
            categories.add(_NATIVE_CATEGORIES[info.name])
        elif info.integration:
            # MCP/domain tools: use integration name as category
            categories.add(info.integration)
    if not categories:
        return ""
    return f"Additional capabilities available via search_tools: {', '.join(sorted(categories))}."
