"""Approval risk classifier: LOW / MEDIUM / HIGH for tool calls."""

from enum import Enum
from typing import Any


class ApprovalRisk(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Tool names that are explicitly high risk (write side effects)
_HIGH_RISK_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
    "create_email_draft",
    "save_memory",
    "save_article",
    "update_memory",
    "append_memory",
})

# Tool name prefixes or patterns that indicate high risk for shell commands
_HIGH_RISK_SHELL_PATTERNS: tuple[str, ...] = (
    "rm ", "rm\t", "mv ", "mv\t", "cp ",
    "chmod ", "chown ", "dd ", "mkfs",
    "truncate ", "shred ",
    "> ", ">>",  # redirection write patterns
)

# Read-only tool name prefixes — LOW risk
_LOW_RISK_PREFIXES: tuple[str, ...] = (
    "read_", "list_", "search_", "find_",
)

# Exact low-risk tool names
_LOW_RISK_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "find_in_files",
    "list_directory",
    "list_memories",
    "read_article_detail",
    "search_knowledge",
    "list_notes",
    "read_note",
    "search_drive_files",
    "read_drive_file",
    "list_emails",
    "search_emails",
    "list_calendar_events",
    "search_calendar_events",
    "web_search",
    "web_fetch",
    "check_capabilities",
    "todo_read",
})


def classify_tool_call(tool_name: str, args: dict[str, Any]) -> ApprovalRisk:
    """Classify the risk level of a tool call.

    HIGH: write_file, edit_file, and shell commands with destructive patterns
    LOW: read-only tools (read_*, list_*, search_*, find_*)
    MEDIUM: everything else
    """
    # Explicit HIGH risk tools
    if tool_name in _HIGH_RISK_TOOLS:
        return ApprovalRisk.HIGH

    # Shell command risk depends on the command content
    if tool_name == "run_shell_command":
        cmd = str(args.get("cmd", "")).strip()
        for pattern in _HIGH_RISK_SHELL_PATTERNS:
            if pattern in cmd:
                return ApprovalRisk.HIGH
        return ApprovalRisk.MEDIUM

    # Explicit LOW risk tools
    if tool_name in _LOW_RISK_TOOLS:
        return ApprovalRisk.LOW

    # Prefix-based LOW risk detection
    for prefix in _LOW_RISK_PREFIXES:
        if tool_name.startswith(prefix):
            return ApprovalRisk.LOW

    # Default: MEDIUM
    return ApprovalRisk.MEDIUM
