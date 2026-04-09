# RESEARCH: mem0 Memory Architecture — Deep Scan

Source: `~/workspace_genai/mem0` (mem0 memory layer for AI agents)
Scan date: 2026-04-07 (verified against code)

See also: [RESEARCH-tools-mem0.md](RESEARCH-tools-mem0.md) for tool surface analysis (MCP tools, REST API, graph tools, Claude plugin hooks).

---

## 1. Memory Class & API Surface

**Memory class** (`mem0/memory/main.py:244`), extends `MemoryBase` (`mem0/memory/base.py:4–64`).

**Constructor** (`main.py:244–305`):
```
Memory(config: MemoryConfig = MemoryConfig())
 → EmbedderFactory.create()               line 250–254
 → VectorStoreFactory.create()            line 255–257
 → LlmFactory.create()                    line 258
 → SQLiteManager()                        line 259 (history DB)
 → RerankerFactory.create()               line 266–269 (optional)
 → GraphStoreFactory.create()             line 275 (optional, if graph_store.config set)
```

**Public API**:

| Method | Line | Signature |
|--------|------|-----------|
| `add()` | 370 | `add(messages, *, user_id, agent_id, run_id, metadata, infer, memory_type, prompt)` |
| `get()` | 719 | `get(memory_id)` |
| `get_all()` | 762 | `get_all(*, user_id, agent_id, run_id, filters, limit=100)` |
| `search()` | 867 | `search(query, *, user_id, agent_id, run_id, limit=100, filters, threshold, rerank=True)` |
| `update()` | 1101 | `update(memory_id, data, metadata)` |
| `delete()` | 1127 | `delete(memory_id)` |
| `delete_all()` | 1158 | `delete_all(user_id, agent_id, run_id)` |
| `history()` | 1194 | `history(memory_id)` |
| `reset()` | 1366 | `reset()` |
| `close()` | 307 | `close()` |

---

## 2. Memory Types & Record Schema

**MemoryType enum** (`configs/enums.py:4–8`):
- `SEMANTIC = "semantic_memory"`
- `EPISODIC = "episodic_memory"`
- `PROCEDURAL = "procedural_memory"`

**MemoryItem model** (`configs/base.py:17–27`):

| Field | Type | Purpose |
|-------|------|---------|
| `id` | str | UUID (`main.py:1216`) |
| `memory` | str | The stored fact text |
| `hash` | str | MD5 hash of memory text (`main.py:1219`) |
| `metadata` | dict | Custom metadata |
| `score` | float | Search relevance score |
| `created_at` | str | ISO timestamp |
| `updated_at` | str | ISO timestamp |

**Additional payload fields** stored in vector store (`main.py:1217–1222`):
- `user_id`, `agent_id`, `run_id` — session scoping
- `actor_id`, `role` — who created the memory (user/assistant)

---

## 3. Add Pipeline — Full Call Chain

```
Memory.add(messages, user_id, ...)                main.py:370–473

 → Step 1: normalize messages                     lines 435–447
     string → [{"role": "user", "content": str}]
     dict → [dict]
     list[dict] → pass through

 → Step 2: parse vision messages                  lines 453–456
     parse_vision_messages() if images present

 → Step 3: parallel execution                     lines 458–465
     ThreadPoolExecutor:
       thread 1: _add_to_vector_store()           line 475
       thread 2: _add_to_graph()                  line 708 (if graph enabled)
```

### Vector store path (`_add_to_vector_store`)

```
 → select extraction prompt                       lines 515–522
     agent_id present + assistant msgs? → AGENT_MEMORY_EXTRACTION_PROMPT (prompts.py:122–173)
     otherwise → USER_MEMORY_EXTRACTION_PROMPT                          (prompts.py:62–120)

 → LLM fact extraction                            lines 527–533
     llm.generate_response(system_prompt, user_prompt)
     response_format: {"type": "json_object"}
     → {"facts": ["fact1", "fact2", ...]}

 → parse + normalize facts                        lines 535–550
     handle code blocks, JSON fallback
     normalize: strings, dicts with "fact"/"text" keys

 → for each fact:
     → embed fact + search vector store            lines 555–577
       limit: 5 candidates per fact (line 572)
       filters: user_id, agent_id, run_id

     → LLM update decision                        lines 590–598
       get_update_memory_messages()
       DEFAULT_UPDATE_MEMORY_PROMPT                prompts.py:175–323
       → {"memory": [{"id": "0", "text": "...", "event": "ADD|UPDATE|DELETE|NONE", "old_memory": "..."}]}

     → execute action:
       ADD (lines 631–640):
         → _create_memory()                        lines 1207–1239
             generate UUID (1216)
             build metadata: hash (MD5), timestamps (1217–1222)
             vector_store.insert() (1224–1228)
             history.add_history(event="ADD") (1229–1238)

       UPDATE (lines 641–658):
         → _update_memory()                        lines 1281–1338
             fetch existing (1285–1291)
             merge metadata, preserve session IDs (1293–1312)
             vector_store.update() (1321–1325)
             history.add_history(event="UPDATE") (1328–1337)

       DELETE (lines 659–667):
         → _delete_memory()                        lines 1340–1364
             vector_store.delete() (1352) — hard delete
             history.add_history(event="DELETE", is_deleted=1) (1353–1363)

       NONE (lines 668–694):
         → update session IDs if agent_id/run_id provided
         → otherwise no-op
```

### Graph store path (`_add_to_graph`)

```
 → extract non-system messages                    line 714
 → MemoryGraph.add(data, filters)                 graph_memory.py:76–94
     → _retrieve_nodes_from_data()                line 84 (LLM entity extraction)
     → _establish_nodes_relations_from_data()     line 85
     → _search_graph_db()                         line 86
     → _get_delete_entities_from_search_output()  line 87
     → _delete_entities()                         line 91 (soft-delete old)
     → _add_entities()                            line 92 (insert new)
     → return {deleted_entities, added_entities}
```

---

## 4. Search Pipeline

```
Memory.search(query, user_id, ...)                main.py:867–965

 → build filters                                  lines 911–924
     merge session IDs + custom filters
     detect advanced operators (AND, OR, NOT, gt, lt, in, etc.)  lines 1051–1061
     _process_metadata_filters() if advanced      lines 920–921

 → parallel execution                             lines 941–952
     ThreadPoolExecutor:
       thread 1: _search_vector_store()           lines 1063–1099
         embed query (1064)
         vector_store.search(query, vectors, limit, filters) (1065)
         promote keys: user_id, agent_id, run_id, actor_id, role (1078–1098)
         apply threshold cutoff (1096)

       thread 2: MemoryGraph.search()             graph_memory.py:96–130
         extract entities from query (110)
         search graph DB (111)
         BM25Okapi reranking (119–122)
         return top-N relationships (127)

 → optional reranking                             lines 955–960
     if rerank=True and reranker configured
     self.reranker.rerank(query, results, limit)

 → response assembly                              lines 962–965
     graph enabled: {"results": vector_results, "relations": graph_results}
     graph disabled: {"results": vector_results}
```

---

## 5. Update, Delete & History

### update()
```
Memory.update(memory_id, data, metadata)          main.py:1101–1125
 → embed new data (1122)
 → _update_memory() (1124) — updates vector store + logs history
```

### delete()
```
Memory.delete(memory_id)                          main.py:1127–1156
 → fetch existing memory (1136–1155)
 → if graph enabled: cleanup graph entities (1141–1153)
 → _delete_memory() (1155)
     vector_store.delete() — hard delete (1352)
     history.add_history(is_deleted=1) — soft-delete in history (1353–1363)
```

### delete_all()
```
Memory.delete_all(user_id, agent_id, run_id)      main.py:1158–1192
 → build filter dict (1167–1178)
 → list all matching memories (1183)
 → delete each individually (1184–1185)
 → if graph: graph.delete_all(filters) (1189–1190)
```

### history()
```
Memory.history(memory_id)                         main.py:1194
 → SQLiteManager.get_history(memory_id)           storage.py:169–197
     SELECT * FROM history WHERE memory_id=? ORDER BY created_at, updated_at
     returns list of dicts
```

---

## 6. History Tracking

**SQLiteManager** (`mem0/memory/storage.py:10–219`)

**History table schema** (`storage.py:104–118`):

| Column | Type | Purpose |
|--------|------|---------|
| `id` | TEXT PK | UUID per history record |
| `memory_id` | TEXT | References the tracked memory |
| `old_memory` | TEXT | Previous content (null for ADD) |
| `new_memory` | TEXT | New content (null for DELETE) |
| `event` | TEXT | ADD, UPDATE, DELETE |
| `created_at` | DATETIME | Memory creation timestamp |
| `updated_at` | DATETIME | Event timestamp |
| `is_deleted` | INTEGER | Soft-delete flag (1=deleted) |
| `actor_id` | TEXT | Who performed the action |
| `role` | TEXT | Actor role (user/assistant) |

**Event logging calls**:
- ADD: `add_history(mem_id, None, text, "ADD", ...)` (`main.py:1229–1238`)
- UPDATE: `add_history(mem_id, old, new, "UPDATE", ...)` (`main.py:1328–1337`)
- DELETE: `add_history(mem_id, old, None, "DELETE", ..., is_deleted=1)` (`main.py:1353–1363`)

---

## 7. Graph Memory (Neo4j)

**MemoryGraph class** (`mem0/memory/graph_memory.py:29–200+`)

### Initialization (`lines 30–75`):
- Connect to Neo4j via LangChain (lines 32–39)
- Initialize embedder (lines 40–42)
- Create indexes on `user_id` + composite with `name` (lines 45–56)

### add() (`lines 76–94`):
```
 → _retrieve_nodes_from_data()              line 84 (LLM entity extraction)
 → _establish_nodes_relations_from_data()   line 85
 → _search_graph_db()                       line 86
 → _get_delete_entities_from_search_output() line 87
 → _delete_entities()                       line 91 (soft-delete old relationships)
 → _add_entities()                          line 92 (insert new)
```

### search() (`lines 96–130`):
```
 → extract entities from query              line 110
 → search graph DB                          line 111
 → BM25Okapi reranking                      lines 119–122
 → return top-N relationships               line 127
```

### delete() (`lines 132–152`):
- Soft-delete: sets `r.valid = false` + `r.invalidated_at = datetime()` (line 428)
- `_search_graph_db()` filters: `WHERE r.valid IS NULL OR r.valid = true` (line 199)

### Activation:
- Enabled when `config.graph_store.config` is not None (`main.py:273–276`)
- Parallel execution with vector ops via `ThreadPoolExecutor`

---

## 8. Vector Store & Embedding Abstraction

### VectorStoreFactory (`utils/factory.py:167–204`)

**24 backends** (`provider_to_class` dict, lines 168–193):
qdrant, chroma, pgvector, milvus, upstash_vector, azure_ai_search, azure_mysql, pinecone, mongodb, redis, valkey, databricks, elasticsearch, vertex_ai_vector_search, opensearch, supabase, weaviate, faiss, langchain, s3_vectors, baidu, cassandra, neptune, turbopuffer

### VectorStoreBase interface (`vector_stores/base.py`):
- `create_col(name, vector_size, distance)`
- `insert(vectors, payloads, ids)`
- `search(query, vectors, limit, filters)`
- `delete(vector_id)`, `update(vector_id, vector, payload)`, `get(vector_id)`
- `list(filters, limit)`, `list_cols()`, `delete_col()`, `col_info()`, `reset()`

### EmbedderFactory (`utils/factory.py:139–164`)

**11 providers**: openai, ollama, huggingface, azure_openai, gemini, vertexai, together, lmstudio, langchain, aws_bedrock, fastembed

### LlmFactory (`utils/factory.py:30–136`)

**18 providers**: ollama, openai, groq, together, aws_bedrock, litellm, azure_openai, openai_structured, anthropic, azure_openai_structured, gemini, deepseek, minimax, xai, sarvam, lmstudio, vllm, langchain

---

## 9. Configuration

**MemoryConfig** (`configs/base.py:30–67`):

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `vector_store` | VectorStoreConfig | factory default | Backend config |
| `llm` | LlmConfig | factory default | LLM provider |
| `embedder` | EmbedderConfig | factory default | Embedding model |
| `history_db_path` | str | `~/.mem0/history.db` | SQLite history path |
| `graph_store` | GraphStoreConfig | factory default | Neo4j / other graph DB |
| `reranker` | RerankerConfig | None | Optional reranking |
| `version` | str | `"v1.1"` | API version |
| `custom_fact_extraction_prompt` | str | None | Override extraction prompt |
| `custom_update_memory_prompt` | str | None | Override update decision prompt |

**Session scoping** (`main.py:159–237`, `_build_filters_and_metadata()`):
- At least one of `user_id`, `agent_id`, `run_id` required per operation (line 224–230)
- All three propagated as metadata + filters on every vector store call
- `actor_id` and `role` tracked for audit

**Custom prompts** (`base.py:59–66`):
- Must include "json" keyword for OpenAI json_object format compatibility (`utils.py:36–58`)

---

## 10. Telemetry & Error Handling

**Telemetry** (`memory/telemetry.py:1–146`):
- PostHog analytics (line 34)
- Gate: `MEM0_TELEMETRY` env var, default "True" (line 13)
- Events: `mem0.init`, `mem0.add`, `mem0.get`, `mem0.search`, `mem0.update`, `mem0.delete`, `mem0.reset`
- Privacy: MD5 hash of user_id/agent_id/run_id before sending (`utils.py:200–215`)

**Error handling**:
- `Mem0ValidationError` for invalid inputs (`main.py:225–230, 442–447`)
- JSON parse fallback: direct parse → `extract_json()` → `remove_code_blocks()` (`main.py:535–550, 604–616`)
- No retry logic in core paths — operations fail fast

---

## 11. Gap Analysis: mem0 vs co-cli

### mem0 has, co-cli does not

| Gap | mem0 | co-cli status | Severity |
|-----|------|---------------|----------|
| **Full mutation history** | Every ADD/UPDATE/DELETE logged to SQLite with old_memory, new_memory, event, actor_id, role (`storage.py:104–118`). `history(memory_id)` API | No old/new value tracking. Partial audit via `updated` timestamp + `provenance` field only | **Medium** — useful for debugging memory drift; co-cli lacks undo/audit trail |
| **LLM-driven fact extraction** | Automatic extraction from conversation messages via `USER_MEMORY_EXTRACTION_PROMPT` → `{"facts": [...]}` (`prompts.py:62–120`). No explicit save needed | co-cli requires explicit `save_memory` tool call or high-confidence auto-signal match (`_extractor.py:141–225`). No conversation-level fact extraction | **Medium** — trade-off: mem0 extracts implicitly (may over-save); co-cli is explicit (may under-save) |
| **Graph memory** | Optional Neo4j entity-relationship store with BM25 reranking (`graph_memory.py:76–152`). Entity extraction, relationship tracking, soft-delete with temporal validity | co-cli has one-hop `related` links (flat file slugs, `memory.py:358–572`). No entity extraction, no relationship types, no graph DB | **Low** — co-cli's one-hop links cover basic relationships. Full graph is heavy infrastructure for the CLI use case |
| **Multi-backend vector store** | 24 vector backends via `VectorStoreFactory` (`factory.py:167–204`). Production-scale options (Qdrant, Pinecone, Milvus, etc.) | Single backend: SQLite FTS5/BM25 + optional sqlite-vec (`_store.py:213`). No vector-DB-as-a-service integration | **Not applicable** — co-cli is local-first; SQLite FTS5 is the right choice for single-user CLI |
| **Reranking** | Optional reranker support (cohere, sentence_transformer, etc.) applied after vector search (`main.py:955–960`) | No reranking. FTS5/BM25 scores used directly | **Low** — marginal quality improvement for additional latency/cost |
| **Per-entity scoping** | `user_id` + `agent_id` + `run_id` on every operation, enforced via `_build_filters_and_metadata()` (`main.py:159–237`) | Two-tier: project-local memory + user-scope articles. No per-agent or per-run scoping | **Low** — co-cli is single-agent; multi-agent scoping not needed |

### co-cli has, mem0 does not

| Advantage | co-cli | mem0 status |
|-----------|--------|-------------|
| **Structured search index with decay** | FTS5/BM25 hybrid + temporal decay scoring (0.6×relevance + 0.4×decay, `memory.py:457–462`). Exponential half-life (`memory.py:221–239`) | No temporal decay. Vector similarity only. Recent and old memories scored equally |
| **Write-time agent-based dedup** | `check_and_save()` (`_save.py:64–100`) compares candidate against manifest before write. Decides SAVE_NEW vs UPDATE | LLM-prompt-driven dedup only — existing memories retrieved, LLM decides ADD/UPDATE/DELETE. No pre-write structural comparison |
| **Rich typed frontmatter** | 14 validated fields (`_frontmatter.py:103–257`): provenance, certainty, auto_category, decay_protected, always_on, related | Flat metadata dict. No typed schema, no provenance tracking, no decay protection |
| **Always-on standing context** | Up to 5 memories with `always_on=True` injected every turn (`agent.py:324–332`) | No injection mechanism. Caller must search and inject manually |
| **Automatic retention** | `enforce_retention()` (`_retention.py:16–52`) — prunes oldest non-protected when total > 200 | No automatic retention. `delete_all()` available but manual. Memory count grows unbounded |
| **Cross-source knowledge search** | `search_knowledge` (`articles.py:161–309`) across memory, articles, Obsidian, Google Drive | Single-source vector search only |
| **Explicit update tools** | `update_memory` (str_replace with guards, `memory.py:830`) + `append_memory` (`memory.py:943`) | `update(memory_id, data)` replaces entire memory text — no surgical edit |
