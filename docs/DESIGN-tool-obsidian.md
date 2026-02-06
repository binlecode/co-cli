# Design: Obsidian Vault Tools

**Status:** Implemented (Batch 2)
**Last Updated:** 2026-02-04

## Overview

The Obsidian tools provide read-only access to a local Obsidian vault, enabling the agent to search, list, and read markdown notes. Designed for knowledge retrieval (RAG) use cases.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Co CLI                                   │
│                                                                  │
│  User: "find notes about project X"                             │
│         │                                                        │
│         ▼                                                        │
│  ┌─────────────────┐                                            │
│  │   Agent.run()   │                                            │
│  │   deps=CoDeps   │                                            │
│  └────────┬────────┘                                            │
│           │ tool call: search_notes(query="project X")          │
│           ▼                                                      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              search_notes()                              │    │
│  │  1. Get vault path from ctx.deps                         │    │
│  │  2. Glob all *.md files                                  │    │
│  │  3. Regex search content                                 │    │
│  │  4. Return matches with snippets                         │    │
│  └────────┬────────────────────────────────────────────────┘    │
└───────────┼──────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Local File System                             │
│  ~/.../ObsidianVault/                                           │
│  ├── Projects/                                                   │
│  │   ├── Project-X.md  ◀── matched                              │
│  │   └── Project-Y.md                                           │
│  ├── Daily/                                                      │
│  │   └── 2026-02-04.md                                          │
│  └── Ideas.md                                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tools

### search_notes

Primary tool for knowledge retrieval. Multi-keyword search with word boundaries.

```
search_notes(query: str, limit: int = 10) -> list[dict]

Args:
    query: Space-separated keywords (AND logic, whole words, case-insensitive)
           Example: "project timeline" finds notes containing BOTH words
    limit: Maximum results (default 10, per RAG best practice)

Returns:
    [{"file": "path/note.md", "snippet": "...context..."}]

Raises:
    ModelRetry: If vault not configured or no matches found
```

**Search Behavior:**

| Query | Matches | Doesn't Match |
|-------|---------|---------------|
| `project` | "the project plan" | "projector repair" |
| `project team` | "project with team" | "project proposal" |

**Processing Flow:**

```
search_notes("project team")
       │
       ▼
┌──────────────────────────────┐
│ Validate vault path          │
│   └── None? ──▶ ModelRetry   │
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Parse keywords               │
│   └── ["project", "team"]    │
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Build word-boundary patterns │
│   └── \bproject\b, \bteam\b  │
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ For each *.md note:          │
│   ├── Read content           │
│   ├── Check ALL patterns     │
│   │   (AND logic)            │
│   └── If all match:          │
│         └── Extract snippet  │
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ No results? ──▶ ModelRetry   │
│   "Try fewer keywords"       │
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Return results[:limit]       │
│   (default 10)               │
└──────────────────────────────┘
```

### list_notes

Browse vault structure, optionally filter by tag.

```
list_notes(tag: str | None = None) -> list[str]

Args:
    tag: Optional tag to filter (e.g. "#project")

Returns:
    List of relative file paths
```

**Use Cases:**
- Browse vault: `list_notes()`
- Find tagged notes: `list_notes("#work")`

### read_note

Read full content of a specific note.

```
read_note(filename: str) -> str

Args:
    filename: Relative path (e.g. "Projects/Project-X.md")

Returns:
    Full note content

Raises:
    ModelRetry: If not found (includes available files for retry)
```

**Security:** Path traversal protection prevents reading outside vault.

---

## Deps Integration

Tools access vault path via `RunContext[CoDeps]`:

```
┌─────────────────────────────────────────┐
│ main.py: create_deps()                  │
│   │                                     │
│   ├── vault_path = Path(settings.       │
│   │       obsidian_vault_path)          │
│   │                                     │
│   └── CoDeps(                           │
│           obsidian_vault_path=vault_path│
│       )                                 │
└─────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────┐
│ tool function                           │
│   │                                     │
│   └── vault = ctx.deps.obsidian_vault_  │
│              path                       │
└─────────────────────────────────────────┘
```

**Why deps, not global settings?**
- Testable (inject test paths)
- Explicit dependencies
- Follows pydantic-ai pattern

---

## Error Handling with ModelRetry

Tools use `ModelRetry` for self-healing:

| Scenario | ModelRetry Message | LLM Action |
|----------|-------------------|------------|
| Vault not configured | "Ask user to set obsidian_vault_path" | Inform user |
| No search results | "Try different keywords or use list_notes" | Retry with broader terms |
| Note not found | "Available notes: [...]. Use exact path" | Retry with correct path |

**Why ModelRetry over error strings?**

```
# Bad - LLM sees error, gives up
return "Error: file not found"

# Good - LLM retries with guidance
raise ModelRetry(
    f"Note '{filename}' not found. "
    f"Available: {available}. Use exact path."
)
```

---

## Configuration

| Setting | Source | Default |
|---------|--------|---------|
| `obsidian_vault_path` | `settings.json` | None |

**Environment variable:** `OBSIDIAN_VAULT_PATH`

**Example settings.json:**
```json
{
  "obsidian_vault_path": "/Users/name/Documents/ObsidianVault"
}
```

---

## Security Model

### Path Traversal Protection

```
read_note("../../etc/passwd")
       │
       ▼
┌──────────────────────────────────────┐
│ safe_path = (vault / filename).resolve()
│                                      │
│ Check: safe_path.is_relative_to(vault)
│   ├── Yes ──▶ Allow read            │
│   └── No  ──▶ ModelRetry            │
│              "Access denied"         │
└──────────────────────────────────────┘
```

### Read-Only Access

All tools are read-only. No write/delete operations.

---

## Comparison with Industry Standard

Based on [Obsidian RAG MCP Server](https://glama.ai/mcp/servers/@claudiogarza/obsidian-rag-mcp) (2026):

| MCP Standard | Co CLI | Status |
|--------------|--------|--------|
| `search_vault` | `search_notes` | ✓ Multi-keyword with word boundaries |
| `search_by_tag` | `list_notes(tag)` | ✓ Implemented |
| `get_note` | `read_note` | ✓ Implemented |
| `get_related` | - | Not implemented |
| `list_recent` | - | Not implemented |

**Search Comparison:**

| Type | Our Implementation | Limitation |
|------|-------------------|------------|
| Substring | ~~`"proj"` matches "projector"~~ | Removed |
| Keyword | `\bproject\b` whole word | ✓ Current |
| Multi-keyword | `project AND team` | ✓ Current |
| Semantic | Embeddings + similarity | Not implemented |

**Future enhancements:**
- `get_related(filename)` - Find notes by backlinks
- `list_recent(days)` - Recently modified notes
- Semantic search with embeddings (requires vector store)

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/tools/obsidian.py` | Tool implementations |
| `co_cli/deps.py` | CoDeps with `obsidian_vault_path` |
| `tests/test_obsidian.py` | Functional tests |
