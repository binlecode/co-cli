"""Tool category frozensets — single source of truth for tool behavioral metadata.

Import from here in all modules that gate on tool names to avoid string drift.
"""

# Tools whose call args (path) are tracked for compaction working-set context.
# Includes file_search whose path arg is a glob identifying a workspace scope
# even though it is not a single path.
FILE_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "file_write",
        "file_patch",
        "file_search",
    }
)
