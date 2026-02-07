# Design: Obsidian Vault Tools

**Status:** Implemented (Batch 2, display-field update 2026-02-06)
**Last Updated:** 2026-02-06

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
search_notes(query: str, limit: int = 10) -> dict[str, Any]

Args:
    query: Space-separated keywords (AND logic, whole words, case-insensitive)
           Example: "project timeline" finds notes containing BOTH words
    limit: Maximum results (default 10, per RAG best practice)

Returns:
    {"display": "**file.md**\n  snippet...", "count": N, "has_more": false}
    Empty results: {"display": "No notes found matching: ...", "count": 0, "has_more": false}

Raises:
    ModelRetry: If vault not configured or empty query
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
│ No results?                  │
│   └── Return {"count": 0}   │
│       (not ModelRetry)       │
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Build display string         │
│   **file.md**                │
│     snippet text...          │
└──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Return {"display": "...",    │
│   "count": N, "has_more": F}│
└──────────────────────────────┘
```

### list_notes

Browse vault structure, optionally filter by tag.

```
list_notes(tag: str | None = None) -> dict[str, Any]

Args:
    tag: Optional tag to filter (e.g. "#project")

Returns:
    {"display": "- file1.md\n- file2.md", "count": N}
    Empty results: {"display": "No notes found.", "count": 0}
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

## Error Handling: ModelRetry vs Empty Result

Following the project-wide design principle (see `docs/TODO-tool-call-stability.md`):

- **`ModelRetry`** = "you called this wrong, fix your parameters"
- **Empty result** = "query was fine, nothing matched"

| Scenario | Response | LLM Action |
|----------|----------|------------|
| Vault not configured | `ModelRetry("Ask user to set obsidian_vault_path")` | Inform user |
| Empty query | `ModelRetry("Provide keywords to search")` | Fix parameters |
| No search results | `{"display": "No notes found matching: ...", "count": 0}` | Report to user |
| No notes in vault | `{"display": "No notes found.", "count": 0}` | Report to user |
| Note not found | `ModelRetry("Available notes: [...]. Use exact path")` | Retry with correct path |
| Path traversal | `ModelRetry("Access denied: path is outside the vault")` | Stop |

**Why empty result for no search matches?**

```python
# Correct — query was valid, nothing matched (consistent with search_drive)
return {"display": "No notes found matching: project", "count": 0, "has_more": False}

# Correct — LLM made a fixable error
raise ModelRetry(f"Note '{filename}' not found. Available: {available}.")
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
