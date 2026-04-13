"""Tool category frozensets — single source of truth for tool behavioral metadata.

Import from here in all modules that gate on tool names to avoid string drift.
"""

# Tools whose path argument is resolved to absolute before execution.
# Used by CoToolLifecycle.before_tool_execute.
PATH_NORMALIZATION_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
    }
)

# Tools whose call args (path) are tracked for compaction working-set context.
# Superset of PATH_NORMALIZATION_TOOLS — includes find_in_files whose pattern
# arg identifies a workspace scope even though it is not a single path.
FILE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "find_in_files",
        "list_directory",
    }
)

# Tools whose results are content-cleared when older than the N most recent
# per-tool returns. Keeps context window lean for high-volume read tools.
COMPACTABLE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "run_shell_command",
        "find_in_files",
        "list_directory",
        "web_search",
        "web_fetch",
        "read_article",
        "read_note",
    }
)
