---
title: "09 — Obsidian Vault Tools"
parent: Tools
nav_order: 2
---

# Design: Obsidian Vault Tools

## 1. What & How

The Obsidian tools provide read-only access to a local Obsidian vault, enabling the agent to search, list, and read markdown notes for knowledge retrieval (RAG). All tools use `RunContext[CoDeps]` with `ModelRetry` for self-healing errors.

```
User: "find notes about project X"
  │
  ▼
Agent.run() → search_notes(query="project X")
  │
  ├── Get vault path from ctx.deps.obsidian_vault_path
  ├── Glob all *.md files
  ├── Regex search with word boundaries (AND logic)
  └── Return matches with snippets
  │
  ▼
Local File System: ~/.../ObsidianVault/
```

## 2. Core Logic

### Tools

**`search_notes(query, limit=10, folder=None, tag=None) → dict`** — Multi-keyword AND search with word boundaries (`\bproject\b`). Optional `folder` narrows the search root; optional `tag` checks both YAML frontmatter tags and inline content tags. Returns `{"display": "...", "count": N, "has_more": bool}`. Empty results return `count=0` (not ModelRetry).

**`list_notes(tag=None) → dict`** — Browse vault structure, optionally filter by tag match in note content. Returns `{"display": "- file1.md\n- file2.md", "count": N}`.

**`read_note(filename) → str`** — Read full content of a specific note. Path traversal protection prevents reading outside vault. Returns raw content string.

### Error Handling

| Scenario | Response | LLM Action |
|----------|----------|------------|
| Vault not configured | `ModelRetry("Ask user to set obsidian_vault_path")` | Inform user |
| Empty query | `ModelRetry("Provide keywords to search")` | Fix parameters |
| Invalid folder path | No matches/empty result | Report no results or retry with correct folder |
| No search results | `{"count": 0}` | Report to user |
| Note not found | `ModelRetry("Available notes: [...]. Use exact path")` | Retry with correct path |
| Path traversal | `ModelRetry("Access denied: path is outside the vault")` | Stop |

### Security

Path traversal protection:
```python
safe_path = (vault / filename).resolve()
if not safe_path.is_relative_to(vault.resolve()):
    raise ModelRetry("Access denied: path is outside the vault.")
```

All tools are read-only — no write/delete operations.

<details>
<summary>Industry comparison</summary>

Based on [Obsidian RAG MCP Server](https://glama.ai/mcp/servers/@claudiogarza/obsidian-rag-mcp):

| MCP Standard | Co CLI | Status |
|--------------|--------|--------|
| `search_vault` | `search_notes` | Multi-keyword with word boundaries |
| `search_by_tag` | `list_notes(tag)` | Implemented |
| `get_note` | `read_note` | Implemented |
| `get_related` | — | Not implemented |

</details>

## 3. Config

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | `None` | Path to Obsidian vault directory |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/obsidian.py` | Tool implementations (`search_notes`, `list_notes`, `read_note`) |
| `co_cli/deps.py` | CoDeps with `obsidian_vault_path` |
| `tests/test_obsidian.py` | Functional tests |
