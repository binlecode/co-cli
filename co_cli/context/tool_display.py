"""Tool display metadata and result formatting for the orchestration layer.

Centralises per-tool display concerns so adding or changing a tool does not
require touching core-loop code for cosmetic reasons.
"""

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_ai.messages import ToolCallPart

# Maps tool name → the args key whose value is shown in the tool-start annotation.
TOOL_START_DISPLAY_ARG: dict[str, str] = {
    "run_shell_command": "cmd",
    "web_search": "query",
    "web_fetch": "url",
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "find_in_files": "pattern",
    "list_directory": "path",
    "save_memory": "content",
    "search_articles": "query",
    "search_knowledge": "query",
    "search_memories": "query",
    "search_notes": "query",
    "read_note": "filename",
    "run_coding_subagent": "task",
    "run_research_subagent": "query",
    "run_analysis_subagent": "question",
    "run_reasoning_subagent": "problem",
    "start_background_task": "command",
    "check_task_status": "task_id",
}


def get_tool_start_args_display(tool_name: str, part: "ToolCallPart") -> str:
    """Return the single-arg display string shown in the tool-start annotation.

    Returns empty string when the tool has no registered display arg or the
    arg value is missing — the frontend falls back to the tool name.
    """
    key = TOOL_START_DISPLAY_ARG.get(tool_name)
    if not key:
        return ""
    val = part.args_as_dict().get(key, "")
    return str(val)[:120]


def format_for_display(content: Any) -> str | dict | None:
    """Format a tool result for the on_tool_complete frontend call.

    Return contract:
    - str with content → return as-is (native tools always produce strings)
    - dict (MCP raw JSON) → return a compact key: val summary string
      - at most 5 entries; values truncated to 60 chars; (+N more) suffix; capped at 300 chars
      - returns None if the resulting summary is empty
    - everything else → return None
    """
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, dict):
        # MCP tools return raw JSON dicts — render as compact key: value summary
        summary = "; ".join(f"{k}: {str(v)[:60]}" for k, v in list(content.items())[:5])
        if len(content) > 5:
            summary += f" (+{len(content) - 5} more)"
        result = summary[:300] or None
        return result
    return None
