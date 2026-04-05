# RESEARCH: Peer Memory Architecture — Four-Way Comparison

Sources: `~/workspace_genai/letta`, `~/workspace_genai/mem0`, `~/workspace_genai/fork-claude-code`, `~/workspace_genai/openclaw`, co-cli codebase
Scan date: 2026-04-05

## 1. System Architectures

| System | Source files reviewed | Observed code facts |
|--------|---------------------|---------------------|
| **letta** | `letta/functions/function_sets/base.py:10–243`, `letta/schemas/block.py:1–150`, `letta/orm/archive.py:1–99`, `letta/orm/passage.py:1–105` | Two-tier memory: **core blocks** (labeled in-context sections) + **archival passages** (vector-indexed long-term). Core block operations at `base.py:10–68`: `create`, `str_replace`, `insert`, `delete`, `rename` — all via a single `memory(agent_state, command, ...)` dispatcher. `BaseBlock` fields at `block.py:13–65`: `value` (str, char-limited), `limit` (int), `label` (str — "human", "persona", etc.), `description`, `tags` (List[str]), `metadata` (dict), `read_only` (bool), `hidden` (bool), `created_by_id`, `last_updated_by_id`. Archival: `archival_memory_insert(content, tags)` and `archival_memory_search(query, tags, tag_match_mode, top_k, start_datetime, end_datetime)` at `base.py:164–243`. `ArchivalPassage` schema at `passage.py:76–105`: `text`, `embedding` (pgvector/CommonVector), `tags`, `metadata_`, `passage_tags` junction table. Archive schema at `archive.py:24–80`: `name`, `description`, `vector_db_provider`, `embedding_config`. |
| **mem0** | `mem0/memory/main.py:244–1175`, `mem0/configs/prompts.py:62–175`, `mem0/memory/storage.py:100–124`, `mem0/memory/graph_memory.py:29–150` | `Memory` class at `main.py:244`. API: `add(messages, user_id, agent_id, run_id, metadata, infer, memory_type, prompt)` (lines 353–456), `search(query, user_id, agent_id, run_id, limit, filters, threshold, rerank)` (lines 850–948), `update(memory_id, data, metadata)` (lines 1084–1108), `delete(memory_id)` (lines 1110–1139), `delete_all(user_id, agent_id, run_id)` (lines 1141–1175), `history(memory_id)` (line 1177+). Fact extraction via LLM: `USER_MEMORY_EXTRACTION_PROMPT` (`prompts.py:62–120`) extracts from user messages only, returns `{"facts": [...]}`. `AGENT_MEMORY_EXTRACTION_PROMPT` (lines 122–173) for assistant memories. `DEFAULT_UPDATE_MEMORY_PROMPT` (line 175+) for add/update/delete decisions. SQLite history table at `storage.py:100–124`: columns `id`, `memory_id`, `old_memory`, `new_memory`, `event`, `created_at`, `updated_at`, `is_deleted`, `actor_id`, `role`. Optional graph memory at `graph_memory.py:29+`: `MemoryGraph.add()` extracts entities/relations into Neo4j, `search()` does entity-relationship search with BM25 reranking, `delete()` soft-deletes relationships. Vector store via `VectorStoreFactory.create()` — multiple backends (Qdrant, FAISS, Weaviate, etc.). Deduplication via embedding similarity + LLM-driven merge decisions (`main.py:498–550`). |
| **fork-cc** | `tools/AgentTool/agentMemory.ts:1–178`, `tools/AgentTool/agentMemorySnapshot.ts:1–150`, `services/autoDream/autoDream.ts:1–200`, `services/autoDream/consolidationPrompt.ts:1–65` | Three-tier scope: **user** (`~/.claude/agent-memory/<agentType>/`), **project** (`.claude/agent-memory/<agentType>/`, VCS-tracked), **local** (`.claude/agent-memory-local/<agentType>/`, not VCS, or `CLAUDE_CODE_REMOTE_MEMORY_DIR`). Entry point: `MEMORY.md` per agent type. API: `getAgentMemoryDir(agentType, scope)`, `getAgentMemoryEntrypoint(agentType, scope)` at `agentMemory.ts:52–177`. Snapshots at `agentMemorySnapshot.ts:31–150`: `snapshot.json` with `updatedAt`, `.snapshot-synced.json` for sync tracking, `checkAgentMemorySnapshot()` returns `'none'|'initialize'|'prompt-update'`. **autoDream consolidation** at `autoDream.ts:73–171`: four trigger gates (cheapest first): (1) time gate — `hours_since_lastConsolidatedAt >= minHours` (default 24h), (2) scan throttle — skip within 10min, (3) session gate — `sessions_touched >= minSessions` (default 5), (4) lock — single-process enforcer. Feature-gated: `tengu_onyx_plover` (GrowthBook). Consolidation prompt at `consolidationPrompt.ts:10–65`: four phases — orient (ls, read index), gather signal (scan logs, grep transcripts), consolidate (merge, convert relative→absolute dates, delete contradicted facts), prune & index (keep `MEMORY.md` under 25KB, ~150 chars/entry). Executed as forked subagent with read-only bash. |
| **openclaw** | Full directory scan of `~/workspace_genai/openclaw/` | No dedicated memory/knowledge system found. OpenClaw is a channel-relay architecture (WhatsApp, Telegram, Slack, Discord). No memory schema, persistent memory storage, or knowledge base in codebase. |

## 2. Storage Format

| System | Core/Working Memory | Long-Term/Archival | Persistence |
|--------|--------------------|--------------------|-------------|
| **letta** | Labeled text blocks with char limits (`BaseBlock.value`). In-context: included in every prompt. Labels: "human", "persona", custom | Vector-indexed passages (`ArchivalPassage.text` + `embedding`). Tags, datetime filtering | PostgreSQL with pgvector extension |
| **mem0** | N/A — no working memory concept. All memories are long-term facts | Vector-embedded facts in configurable store (Qdrant, FAISS, Weaviate, etc.) + optional Neo4j graph | Vector store + SQLite history + optional Neo4j |
| **fork-cc** | Markdown files per agent type. `MEMORY.md` as index. No char limit enforced at storage (25KB soft limit at consolidation) | Same format — archival is just older entries in the same files | Filesystem (`.claude/agent-memory/`) + JSON snapshots |
| **co-cli** | Markdown files with YAML frontmatter in `.co-cli/memory/`. Kinds: `memory`, `article` | Same format — articles serve as long-form reference | Filesystem + FTS5 index in `search.db` via `KnowledgeIndex` |

## 3. Memory Operations (CRUD)

| Operation | letta | mem0 | fork-cc | co-cli |
|-----------|-------|------|---------|--------|
| **Create** | `memory(state, "create", path, description, file_text)` | `add(messages, infer=True)` — LLM extracts facts | FileWrite tool to memory dir | `save_memory` tool → write `.md` file + FTS5 index |
| **Read** | Block content included in every prompt (in-context) | `search(query, limit, filters, threshold)` — semantic similarity | FileRead on `MEMORY.md` + topic files | `recall_memory` tool → FTS5/BM25 search |
| **Update** | `memory(state, "str_replace", path, old_string, new_string)` | `update(memory_id, data, metadata)` — LLM decides merge | FileWrite/Edit to existing file | `update_memory` tool → overwrite `.md` file + re-index |
| **Delete** | `memory(state, "delete", path)` | `delete(memory_id)` / `delete_all(user_id, ...)` | FileWrite (remove content) | `delete_memory` (not found — verify) |
| **Search** | `archival_memory_search(query, tags, top_k, datetime range)` — semantic | `search(query, filters, rerank)` — semantic + optional reranking | Grep transcripts during consolidation | `search_memories` → FTS5/BM25; `recall_memory` → same |
| **History** | Not observed — no mutation history | `history(memory_id)` → SQLite `history` table with `old_memory`, `new_memory`, `event` | Snapshot `updatedAt` timestamps | Not observed — no mutation history |

## 4. Memory Lifecycle

| Aspect | letta | mem0 | fork-cc | co-cli |
|--------|-------|------|---------|--------|
| **Consolidation** | Not observed — archival passages are permanent | Not observed — version history via SQLite but no auto-consolidation | **autoDream**: LLM consolidation pass. Triggers: 24h + 5 sessions. Phases: orient → gather signal → consolidate → prune. Keeps index under 25KB, ~150 chars/entry. Forked subagent with read-only bash (`consolidationPrompt.ts:10–65`) | `memory_consolidation_top_k` and `memory_consolidation_timeout_seconds` config fields exist on `CoConfig` (`deps.py:140–141`). Consolidation logic in memory tools |
| **Deduplication** | Not observed at core block level | LLM-driven: embedding similarity finds near-duplicates, LLM decides add/update/delete (`main.py:498–550`) | autoDream consolidation phase deletes contradicted facts | `memory_dedup_window_days` and `memory_dedup_threshold` config fields on `CoConfig` (`deps.py:137–138`) |
| **Scoping** | Per-agent (agent_state carries block ownership) | Per-entity: `user_id`, `agent_id`, `run_id` filters on all operations (`main.py:353`) | Three-tier: user scope (cross-project), project scope (VCS), local scope (not VCS) (`agentMemory.ts:52–177`) | Project-local (`.co-cli/memory/`). No user-scope or cross-project sharing |
| **Injection** | Core blocks always in prompt. Archival via tool call | External — caller retrieves and injects | Read at session start; referenced by model as needed | `inject_opening_context` history processor: FTS5/BM25 recall on every new user turn (`_history.py:353–411`). Max chars: `memory_injection_max_chars` config |
| **Cleanup** | Archival passages permanent | `delete_all()` available. Soft-delete via `is_deleted` flag | autoDream prunes during consolidation | Not observed — no TTL or auto-cleanup |

## 5. Extraction & Intelligence

| Aspect | letta | mem0 | fork-cc | co-cli |
|--------|-------|------|---------|--------|
| **Extraction method** | Manual — agent calls `memory()` tool explicitly | LLM-driven: `USER_MEMORY_EXTRACTION_PROMPT` extracts facts from messages (`prompts.py:62–120`). Returns `{"facts": [...]}` | Manual — model writes to memory files via tools. autoDream consolidation gathers signal from transcripts | Manual — model calls `save_memory` tool |
| **Update intelligence** | Manual str_replace — agent decides what to change | LLM decides add/update/delete via `DEFAULT_UPDATE_MEMORY_PROMPT` (`prompts.py:175+`). Semantic dedup via embeddings | autoDream LLM: merge new signal, convert relative→absolute dates, delete contradicted facts (`consolidationPrompt.ts:10–65`) | Agent decides; `memory_dedup_threshold` config for dedup |
| **Graph/relational** | Not observed | Optional: `MemoryGraph` in Neo4j. Entity-relationship extraction + BM25 reranking (`graph_memory.py:29+`) | Not observed | Not observed |
