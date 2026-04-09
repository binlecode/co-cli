# RESEARCH: mem0 Tool Surface vs co-cli

> Source-code-driven comparison only.
> Records only what is present in `~/workspace_genai/mem0/`, what is present in current `co-cli`, and the direct differences observed in source.

## 1. Scope

Compared code:

- `co-cli`
- `~/workspace_genai/mem0/`

Main mem0 files checked:

- `mem0/memory/main.py` (Memory + AsyncMemory classes)
- `mem0/memory/graph_memory.py` (MemoryGraph)
- `mem0/graphs/tools.py` (graph tool definitions)
- `mem0/memory/storage.py` (SQLiteManager history)
- `mem0/memory/utils.py` (fact extraction helpers)
- `mem0/configs/base.py` (MemoryConfig, MemoryItem)
- `mem0/configs/prompts.py` (extraction + update prompts)
- `mem0/configs/enums.py` (MemoryType enum)
- `mem0/utils/factory.py` (LLM, embedder, vector, graph factories)
- `mem0/client/main.py` (MemoryClient REST client)
- `openmemory/api/app/mcp_server.py` (MCP server)
- `server/main.py` (FastAPI REST server)
- `mem0-plugin/hooks/hooks.json` (Claude plugin hooks)

Main co-cli files checked:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [tool_search.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_search.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

---

## 2. Verified Facts About mem0

### 2.1 Core Memory API (Python)

`Memory` class (`main.py:244`) is the primary tool surface. All operations require at least one of `user_id`, `agent_id`, or `run_id` for scoping (`main.py:224–230`).

**10 public methods** on `Memory`:

| Method | Line | Signature | Purpose |
|--------|------|-----------|---------|
| `add()` | 370 | `add(messages, *, user_id, agent_id, run_id, metadata, infer, memory_type, prompt)` | Create memories from messages; LLM extraction when `infer=True` |
| `get()` | 719 | `get(memory_id)` | Retrieve single memory by UUID |
| `get_all()` | 762 | `get_all(*, user_id, agent_id, run_id, filters, limit=100)` | List all memories for a scope |
| `search()` | 867 | `search(query, *, user_id, agent_id, run_id, limit=100, filters, threshold, rerank=True)` | Vector similarity search with optional reranking |
| `update()` | 1101 | `update(memory_id, data, metadata)` | Replace entire memory content |
| `delete()` | 1127 | `delete(memory_id)` | Hard-delete single memory from vector store |
| `delete_all()` | 1158 | `delete_all(user_id, agent_id, run_id)` | Bulk delete all memories for scope |
| `history()` | 1194 | `history(memory_id)` | Get mutation history from SQLite |
| `reset()` | 1366 | `reset()` | Wipe all vector + graph stores |
| `chat()` | 1397 | `chat(query)` | Not implemented — raises `NotImplementedError` |

`AsyncMemory` class (`main.py:1401–2571`) mirrors all 10 methods with `async/await`.

### 2.2 Memory Types

`MemoryType` enum (`configs/enums.py:4–8`):
- `SEMANTIC = "semantic_memory"` (default)
- `EPISODIC = "episodic_memory"`
- `PROCEDURAL = "procedural_memory"` — handled by `_create_procedural_memory()` (`main.py:1241–1280`), requires `agent_id`

### 2.3 LLM-Driven Fact Extraction Pipeline

The `add()` pipeline (when `infer=True`):

1. **Normalize messages** (`main.py:435–447`): str → `[{"role": "user", "content": str}]`
2. **LLM fact extraction** (`main.py:527–533`): uses `USER_MEMORY_EXTRACTION_PROMPT` (`prompts.py:62–120`) or `AGENT_MEMORY_EXTRACTION_PROMPT` (`prompts.py:122–173`) depending on whether `agent_id` is present with assistant messages. Returns `{"facts": ["fact1", ...]}`
3. **Per-fact dedup** (`main.py:555–577`): embed each fact, search vector store for 5 nearest candidates
4. **LLM update decision** (`main.py:590–598`): `DEFAULT_UPDATE_MEMORY_PROMPT` (`prompts.py:175–323`) decides per fact: `ADD`, `UPDATE`, `DELETE`, or `NONE`
5. **Execute action**: ADD creates new UUID + MD5 hash + timestamps (`main.py:631–640`); UPDATE merges metadata (`main.py:641–658`); DELETE hard-removes from vector store (`main.py:659–667`)

When `infer=False`: messages stored verbatim without extraction.

### 2.4 Graph Memory Tools

`graphs/tools.py` defines 5 tool specs in OpenAI function-calling JSON format (plus `_STRUCT_` variants with `"strict": True`):

| Tool | Line | Parameters | Purpose |
|------|------|-----------|---------|
| `add_graph_memory` | 28 | source, destination, relationship, source_type, destination_type | Add entity relationship |
| `update_graph_memory` | 1 | source, destination, relationship | Update relationship |
| `delete_graph_memory` | 310 (struct), 342 (non-struct) | source, relationship, destination | Delete relationship |
| `establish_relationships` | 85 | entities: [{source, relationship, destination}] | Bulk relationship creation |
| `extract_entities` | 124 | entities: [{entity, entity_type}] | Entity extraction |
| `noop` | 70 | (none) | No-op placeholder |

These are consumed internally by `MemoryGraph` (`graph_memory.py:76–94`) via LLM function-calling during `_add_to_graph()`. They are not user-facing tools.

`MemoryGraph.search()` (`graph_memory.py:96–130`) uses BM25Okapi reranking on graph relationships.

Graph store providers via `GraphStoreFactory` (`factory.py:212–234`): neo4j (default), memgraph, neptune, neptunedb, kuzu, apache_age — **6 backends**.

### 2.5 MCP Server (OpenMemory)

`openmemory/api/app/mcp_server.py` uses `mcp.server.fastmcp.FastMCP` (`line 34`):

| MCP Tool | Line | Parameters | Purpose |
|----------|------|-----------|---------|
| `add_memories` | 65 | `text: str, infer: bool = True` | Add memory with optional LLM extraction |
| `search_memory` | 149 | `query: str` | Vector search with ACL filtering |
| `list_memories` | 228 | (none) | List all accessible memories |
| `delete_memories` | 297 | `memory_ids: list[str]` | Batch delete by IDs |
| `delete_all_memories` | 371 | (none) | Delete all accessible memories |

All MCP tools enforce per-user + per-app ACL via `check_memory_access_permissions()` and log access to `MemoryAccessLog`.

### 2.6 FastAPI REST Server

`server/main.py` — 10 endpoints:

| Endpoint | Method | Line | Purpose |
|----------|--------|------|---------|
| `/configure` | POST | 142 | Set memory configuration at runtime |
| `/memories` | POST | 150 | Create memories |
| `/memories` | GET | 165 | List memories (requires scope ID) |
| `/memories/{id}` | GET | 185 | Get single memory |
| `/search` | POST | 195 | Search memories |
| `/memories/{id}` | PUT | 206 | Update memory |
| `/memories/{id}/history` | GET | 224 | Get mutation history |
| `/memories/{id}` | DELETE | 234 | Delete single memory |
| `/memories` | DELETE | 245 | Bulk delete by scope |
| `/reset` | POST | 266 | Reset all memories |

Authentication via `X-API-Key` header, validated with `secrets.compare_digest()` (`server/main.py:93–108`).

### 2.7 REST Client (Cloud API)

`MemoryClient` (`client/main.py:24`) wraps the hosted mem0 platform API via `httpx`:

Core CRUD: `add`, `get`, `get_all`, `search`, `update`, `delete`, `delete_all`, `history`, `reset`.

Extended operations not on local `Memory`:

| Method | Purpose |
|--------|---------|
| `batch_update(memories)` | Bulk update multiple memories |
| `batch_delete(memories)` | Bulk delete multiple memories |
| `create_memory_export(schema)` | Export memories with custom schema |
| `get_memory_export()` | Retrieve export results |
| `get_summary(filters)` | Get memory summary |
| `users()` | List users |
| `delete_users()` | Delete user records |
| `feedback()` | Submit feedback |
| `get_webhooks()` | List webhooks |
| `create_webhook()` | Create webhook |
| `update_webhook()` | Update webhook |
| `delete_webhook()` | Delete webhook |

`AsyncMemoryClient` (`client/main.py:965`) mirrors the sync client.

### 2.8 Claude Plugin Hooks

`mem0-plugin/hooks/hooks.json` defines 6 lifecycle hooks:

| Hook | Trigger | Purpose |
|------|---------|---------|
| `SessionStart` | startup, resume, compact | Load mem0 context |
| `PreToolUse` | Write, Edit | Block memory writes during tool calls |
| `PreCompact` | before compaction | Save session state to mem0 |
| `Stop` | session end | Cleanup |
| `UserPromptSubmit` | user message | Search memories on each prompt |
| `TaskCompleted` | task done | Capture task completion memories |

### 2.9 History & Audit Trail

`SQLiteManager` (`storage.py:10–219`) tracks every mutation:

| Column | Type | Purpose |
|--------|------|---------|
| `id` | TEXT PK | UUID per history record |
| `memory_id` | TEXT | References the tracked memory |
| `old_memory` | TEXT | Previous content (null for ADD) |
| `new_memory` | TEXT | New content (null for DELETE) |
| `event` | TEXT | ADD, UPDATE, DELETE |
| `created_at` | DATETIME | Memory creation timestamp |
| `updated_at` | DATETIME | Event timestamp |
| `is_deleted` | INTEGER | Soft-delete flag |
| `actor_id` | TEXT | Who performed the action |
| `role` | TEXT | Actor role (user/assistant) |

### 2.10 Provider Counts

| Abstraction | Count | Factory Location |
|-------------|-------|-----------------|
| LLM providers | 17+ | `factory.py:30–136` |
| Embedder providers | 12 | `factory.py:139–164` |
| Vector store backends | 24 | `factory.py:167–210` |
| Graph store backends | 6 | `factory.py:212–234` |
| Reranker providers | 5 | `factory.py:237–275` |

### 2.11 Search Filter Operators

`search()` supports rich metadata filtering (`main.py:889–904`):
- Exact match: `{"key": "value"}`
- Operators: `eq`, `ne`, `in`, `nin`, `gt`, `gte`, `lt`, `lte`, `contains`, `icontains`, wildcard
- Logical: `AND`, `OR`, `NOT`

Advanced filters processed by `_process_metadata_filters()` (`main.py:967`).

---

## 3. Tool Surface Comparison: mem0 vs co-cli

### 3.1 Tools mem0 has, co-cli does not

| Tool / Capability | mem0 Implementation | co-cli Status | Relevance to co-cli |
|---|---|---|---|
| **LLM-driven fact extraction** | `add(infer=True)` runs extraction prompt + per-fact dedup against vector store (`main.py:527–598`) | Explicit `save_memory` + confidence-based auto-save (`_extractor.py`). No conversation-level fact extraction | **Medium** — co-cli's explicit save is intentional; implicit extraction risks over-saving |
| **Full mutation history** | `history(memory_id)` returns all ADD/UPDATE/DELETE events with old/new values (`storage.py:104–118`) | No mutation history. `updated` timestamp + `provenance` field only | **Medium** — useful for debugging memory drift; co-cli lacks undo |
| **Batch operations** | `batch_update()`, `batch_delete()` on MemoryClient (`client/main.py:515, 542`) | Single-record operations only | **Low** — co-cli's memory counts (~200 max) don't need batch ops |
| **Memory export** | `create_memory_export(schema)`, `get_memory_export()` (`client/main.py:568, 595`) | No export API | **Low** — cloud platform feature |
| **Summary generation** | `get_summary(filters)` (`client/main.py:614`) | No summary tool | **Low** — cloud platform feature |
| **Runtime config swap** | `POST /configure` replaces `MemoryConfig` at runtime (`server/main.py:142–147`) | Config is bootstrap-only, immutable after init | **Not applicable** — co-cli's config lifecycle is intentionally fixed |
| **Multi-backend vector search** | 24 vector backends via factory (`factory.py:167–210`) | SQLite FTS5/BM25 only | **Not applicable** — co-cli is local-first |
| **Reranking** | 5 reranker providers post-search (`main.py:955–960`) | No reranking | **Low** — marginal quality for additional cost |
| **Graph memory** | Neo4j + 5 other graph backends, entity extraction, BM25 reranking on relationships (`graph_memory.py`) | One-hop `related` links (flat file slugs) | **Low** — full graph is heavy infrastructure for CLI |
| **Per-entity scoping** | `user_id` + `agent_id` + `run_id` on every operation (`main.py:224–230`) | Two-tier: project-local + user-scope. No per-agent/run scoping | **Low** — co-cli is single-agent |
| **Webhook notifications** | CRUD on webhooks (`client/main.py:778–893`) | No webhooks | **Not applicable** — cloud platform feature |
| **MCP server** | 5 MCP tools with ACL + access logging (`openmemory/api/app/mcp_server.py`) | MCP client (connects to servers), no MCP server | **Low** — co-cli consumes MCP, doesn't serve it |

### 3.2 Tools co-cli has, mem0 does not

| Tool / Capability | co-cli Implementation | mem0 Status |
|---|---|---|
| **Structured search with temporal decay** | FTS5/BM25 hybrid + exponential half-life decay (0.6×relevance + 0.4×decay) | No temporal decay; vector similarity only |
| **Write-time agent-based dedup** | `check_and_save()` compares against manifest before write | LLM-prompt-only dedup at extraction time |
| **Rich typed frontmatter** | 14 validated fields: provenance, certainty, auto_category, decay_protected, always_on, related | Flat metadata dict, no schema |
| **Always-on standing context** | Up to 5 `always_on=True` memories injected every turn | No injection mechanism — caller must search and inject |
| **Automatic retention** | `enforce_retention()` prunes oldest non-protected when total > 200 | No automatic retention; grows unbounded |
| **Cross-source knowledge search** | `search_knowledge` across memory, articles, Obsidian, Google Drive | Single-source vector search only |
| **Surgical memory editing** | `update_memory` (str_replace with guards) + `append_memory` | `update()` replaces entire memory text |
| **Tool search / discovery** | `search_tools` with token-overlap scoring + deferred unlock | No tool discovery mechanism |
| **Plan mode** | (planned) enter/exit plan mode restricting to read-only tools | No plan mode concept |
| **Structured user questions** | (planned) structured question → answer collection | No structured input mechanism |

### 3.3 Tool Surface Summary

mem0 is a **memory infrastructure platform** — its tool surface centers on CRUD + search across many backends, with LLM-driven extraction as the key differentiator. It serves as a memory layer for other agent frameworks.

co-cli is a **CLI agent** — its tool surface centers on file operations, shell execution, web search, knowledge management, and tool discovery. Memory is one subsystem, not the product.

The tool surfaces overlap only in memory CRUD. mem0's tools that are relevant to the co-cli tool-surface gap analysis:

1. **Structured user input**: mem0 has none — not a gap it addresses
2. **Plan mode**: mem0 has none
3. **Tool search**: mem0 has none — it's not an agent framework
4. **MCP resources**: mem0 serves MCP tools but does not consume MCP resources
5. **Task tracking**: mem0 has none — it's a memory layer, not a task coordinator

**Conclusion**: mem0 does not contribute to any of the 5 tool-surface gaps identified in the TODO. Its relevance is to the **memory subsystem** comparison, not the general-assistant tool surface.

---

## 4. co-cli Differences from mem0 (Existing Comparison)

### What co-cli could learn from mem0 (actionable)

1. **Mutation history** — mem0's SQLite history table with old/new values and event types is a clean model for debugging memory changes. co-cli could add a lightweight audit log without the full infrastructure.

2. **LLM-driven dedup at write time** — mem0's per-fact "ADD/UPDATE/DELETE/NONE" decision via LLM is more nuanced than co-cli's manifest comparison. Worth evaluating for the memory subsystem separately.

### What co-cli should not adopt from mem0

1. **Multi-backend abstraction** — 24 vector stores, 17 LLM providers. co-cli is local-first; SQLite FTS5 is the right choice.
2. **Graph memory** — 6 graph backends. Over-engineered for CLI use case.
3. **Cloud platform features** — webhooks, export, summary, user management. Not relevant to local CLI.
