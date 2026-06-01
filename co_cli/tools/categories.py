"""Tool category frozensets — single source of truth for tool behavioral metadata.

Import from here in all modules that gate on tool names to avoid string drift.
"""

# Tools whose path argument is resolved to absolute before execution.
# Used by CoToolLifecycle.before_tool_execute.
# file_read is intentionally absent: it is multi-root (file_search_roots), so it
# resolves its own raw path via enforce_read_boundary — pre-joining to workspace_dir
# would make a vault-relative/absolute extra-root path unreachable. file_write and
# file_patch stay workspace-anchored (write scope never widens).
PATH_NORMALIZATION_TOOLS: frozenset[str] = frozenset(
    {
        "file_write",
        "file_patch",
    }
)

# Tools whose call args (path) are tracked for compaction working-set context.
# Superset of PATH_NORMALIZATION_TOOLS — includes file_search whose path
# arg is a glob identifying a workspace scope even though it is not a single path.
FILE_TOOLS: frozenset[str] = frozenset(
    {
        "file_read",
        "file_write",
        "file_patch",
        "file_search",
    }
)
