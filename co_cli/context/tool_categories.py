"""Tool category frozensets — single source of truth for tool behavioral metadata.

Import from here in all modules that gate on tool names to avoid string drift.
"""

# Tools whose path argument is resolved to absolute before execution.
# Used by CoToolLifecycle.before_tool_execute.
PATH_NORMALIZATION_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "file_write",
        "file_patch",
        "file_find",
        "file_glob",
    }
)

# Tools whose call args (path) are tracked for compaction working-set context.
# Superset of PATH_NORMALIZATION_TOOLS — includes file_search whose pattern
# arg identifies a workspace scope even though it is not a single path.
FILE_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "file_write",
        "file_patch",
        "file_search",
        "file_find",
        "file_grep",
        "file_glob",
    }
)

# Tools whose results are content-cleared when older than the N most recent
# per-tool returns. Keeps context window lean for high-volume read tools.
COMPACTABLE_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "shell",
        "file_search",
        "file_find",
        "file_grep",
        "file_glob",
        "web_search",
        "web_fetch",
        "knowledge_article_read",
        "obsidian_read",
    }
)
