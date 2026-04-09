# RESEARCH: openclaw Memory Architecture — Deep Scan

Source: `~/workspace_genai/openclaw` (OpenClaw multi-channel agent framework)
Scan date: 2026-04-06

---

## 1. Architecture Overview

Full memory engine via Memory Host SDK (`packages/memory-host-sdk/`). Three subsystems:
- **Indexing**: file discovery → chunking → embedding → SQLite (per-agent store)
- **Search**: hybrid vector+FTS5 with temporal decay + MMR
- **Dreaming**: three-phase background consolidation (Light/Deep/REM)

Additional: session memory hook for automatic transcript capture, multi-provider embeddings with batch processing.

---

## 2. SQLite Schema

Source: `src/memory-host-sdk/host/memory-schema.ts`, function `ensureMemoryIndexSchema()` (lines 3–89)

### Tables

**meta** (lines 11–16):
```sql
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

**files** (lines 17–25):
```sql
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'memory',
  hash TEXT NOT NULL,
  mtime INTEGER NOT NULL,
  size INTEGER NOT NULL
);
```

**chunks** (lines 26–39):
```sql
CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'memory',
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  hash TEXT NOT NULL,
  model TEXT NOT NULL,
  text TEXT NOT NULL,
  embedding TEXT NOT NULL,    -- JSON string, not BLOB or vector extension
  updated_at INTEGER NOT NULL
);
```

**FTS5 virtual table** (lines 64–74):
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS [ftsTable] USING fts5(
  text,
  id UNINDEXED, path UNINDEXED, source UNINDEXED,
  model UNINDEXED, start_line UNINDEXED, end_line UNINDEXED
  [,tokenize='trigram case_sensitive 0']   -- if tokenizer === 'trigram'
);
```
Tokenizer options: `unicode61` (default) or `trigram` (lines 62–63)

**Embedding cache** (lines 41–56, created if `params.cacheEnabled`):
```sql
CREATE TABLE IF NOT EXISTS [embeddingCacheTable] (
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  provider_key TEXT NOT NULL,
  hash TEXT NOT NULL,
  embedding TEXT NOT NULL,
  dims INTEGER,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (provider, model, provider_key, hash)
);
```

### Indexes (lines 54–86)
- `idx_embedding_cache_updated_at` on cache table (line 54)
- `idx_chunks_path` on chunks table (line 85)
- `idx_chunks_source` on chunks table (line 86)

---

## 3. Dreaming Consolidation

Source: `src/memory-host-sdk/dreaming.ts`

### Global defaults (lines 7–41)

| Constant | Line | Value |
|----------|------|-------|
| `DEFAULT_MEMORY_DREAMING_ENABLED` | 7 | `false` |
| `DEFAULT_MEMORY_DREAMING_TIMEZONE` | 8 | `undefined` |
| `DEFAULT_MEMORY_DREAMING_VERBOSE_LOGGING` | 9 | `false` |
| `DEFAULT_MEMORY_DREAMING_STORAGE_MODE` | 10 | `"inline"` |
| `DEFAULT_MEMORY_DREAMING_SEPARATE_REPORTS` | 11 | `false` |
| `DEFAULT_MEMORY_DREAMING_FREQUENCY` | 12 | `"0 3 * * *"` |
| `DEFAULT_MEMORY_DREAMING_SPEED` | 39 | `"balanced"` |
| `DEFAULT_MEMORY_DREAMING_THINKING` | 40 | `"medium"` |
| `DEFAULT_MEMORY_DREAMING_BUDGET` | 41 | `"medium"` |

### Light dreaming (lines 14–17)

| Constant | Line | Value |
|----------|------|-------|
| `DEFAULT_MEMORY_LIGHT_DREAMING_CRON_EXPR` | 14 | `"0 */6 * * *"` (every 6h) |
| `DEFAULT_MEMORY_LIGHT_DREAMING_LOOKBACK_DAYS` | 15 | `2` |
| `DEFAULT_MEMORY_LIGHT_DREAMING_LIMIT` | 16 | `100` |
| `DEFAULT_MEMORY_LIGHT_DREAMING_DEDUPE_SIMILARITY` | 17 | `0.9` |

Config type `MemoryLightDreamingConfig` (lines 67–75):
- `enabled`, `cron`, `lookbackDays`, `limit`, `dedupeSimilarity`
- `sources: MemoryLightDreamingSource[]` — values: `"daily"`, `"sessions"`, `"recall"`
- `execution: MemoryDreamingExecutionConfig`

Default sources (line 133): `["daily", "sessions", "recall"]`

### Deep dreaming (lines 19–32)

| Constant | Line | Value |
|----------|------|-------|
| `DEFAULT_MEMORY_DEEP_DREAMING_CRON_EXPR` | 19 | `"0 3 * * *"` (daily 3am) |
| `DEFAULT_MEMORY_DEEP_DREAMING_LIMIT` | 20 | `10` |
| `DEFAULT_MEMORY_DEEP_DREAMING_MIN_SCORE` | 21 | `0.8` |
| `DEFAULT_MEMORY_DEEP_DREAMING_MIN_RECALL_COUNT` | 22 | `3` |
| `DEFAULT_MEMORY_DEEP_DREAMING_MIN_UNIQUE_QUERIES` | 23 | `3` |
| `DEFAULT_MEMORY_DEEP_DREAMING_RECENCY_HALF_LIFE_DAYS` | 24 | `14` |
| `DEFAULT_MEMORY_DEEP_DREAMING_MAX_AGE_DAYS` | 25 | `30` |

**Recovery sub-config** (lines 27–32):

| Constant | Line | Value |
|----------|------|-------|
| `RECOVERY_ENABLED` | 27 | `true` |
| `RECOVERY_TRIGGER_BELOW_HEALTH` | 28 | `0.35` |
| `RECOVERY_LOOKBACK_DAYS` | 29 | `30` |
| `RECOVERY_MAX_CANDIDATES` | 30 | `20` |
| `RECOVERY_MIN_CONFIDENCE` | 31 | `0.9` |
| `RECOVERY_AUTO_WRITE_MIN_CONFIDENCE` | 32 | `0.97` |

Config type `MemoryDeepDreamingConfig` (lines 86–98):
- `enabled`, `cron`, `limit`, `minScore`, `minRecallCount`, `minUniqueQueries`, `recencyHalfLifeDays`, `maxAgeDays`
- `sources: MemoryDeepDreamingSource[]` — values: `"daily"`, `"memory"`, `"sessions"`, `"logs"`, `"recall"`
- `recovery: MemoryDeepDreamingRecoveryConfig` (lines 77–84)
- `execution: MemoryDreamingExecutionConfig`

Default sources (line 139): `["daily", "memory", "sessions", "logs", "recall"]`

### REM dreaming (lines 34–37)

| Constant | Line | Value |
|----------|------|-------|
| `DEFAULT_MEMORY_REM_DREAMING_CRON_EXPR` | 34 | `"0 5 * * 0"` (Sunday 5am) |
| `DEFAULT_MEMORY_REM_DREAMING_LOOKBACK_DAYS` | 35 | `7` |
| `DEFAULT_MEMORY_REM_DREAMING_LIMIT` | 36 | `10` |
| `DEFAULT_MEMORY_REM_DREAMING_MIN_PATTERN_STRENGTH` | 37 | `0.75` |

Config type `MemoryRemDreamingConfig` (lines 100–108):
- `enabled`, `cron`, `lookbackDays`, `limit`, `minPatternStrength`
- `sources: MemoryRemDreamingSource[]` — values: `"memory"`, `"daily"`, `"deep"`
- `execution: MemoryDreamingExecutionConfig`

Default sources (line 145): `["memory", "daily", "deep"]`

### Execution config (lines 52–60)

```typescript
type MemoryDreamingExecutionConfig = {
  speed: "fast" | "balanced" | "slow";
  thinking: "low" | "medium" | "high";
  budget: "cheap" | "medium" | "expensive";
  model?: string;
  maxOutputTokens?: number;
  temperature?: number;
  timeoutMs?: number;
}
```

### Config resolution

- `resolveMemoryDreamingConfig()` (lines 322–478) — returns full `MemoryDreamingConfig`
- `resolveMemoryDeepDreamingConfig()` (lines 480–496)
- `resolveMemoryLightDreamingConfig()` (lines 498–514)
- `resolveMemoryRemDreamingConfig()` (lines 516–532)
- `resolveMemoryDreamingWorkspaces()` (lines 569–606) — per-workspace + per-agent resolution

---

## 4. Memory Search

Source: `src/agents/memory-search.ts`

### Constants (lines 97–113)

| Constant | Line | Value |
|----------|------|-------|
| `DEFAULT_CHUNK_TOKENS` | 97 | `400` |
| `DEFAULT_CHUNK_OVERLAP` | 98 | `80` |
| `DEFAULT_WATCH_DEBOUNCE_MS` | 99 | `1500` |
| `DEFAULT_SESSION_DELTA_BYTES` | 100 | `100_000` |
| `DEFAULT_SESSION_DELTA_MESSAGES` | 101 | `50` |
| `DEFAULT_MAX_RESULTS` | 102 | `6` |
| `DEFAULT_MIN_SCORE` | 103 | `0.35` |
| `DEFAULT_HYBRID_ENABLED` | 104 | `true` |
| `DEFAULT_HYBRID_VECTOR_WEIGHT` | 105 | `0.7` |
| `DEFAULT_HYBRID_TEXT_WEIGHT` | 106 | `0.3` |
| `DEFAULT_HYBRID_CANDIDATE_MULTIPLIER` | 107 | `4` |
| `DEFAULT_MMR_ENABLED` | 108 | `false` |
| `DEFAULT_MMR_LAMBDA` | 109 | `0.7` |
| `DEFAULT_TEMPORAL_DECAY_ENABLED` | 110 | `false` |
| `DEFAULT_TEMPORAL_DECAY_HALF_LIFE_DAYS` | 111 | `30` |
| `DEFAULT_CACHE_ENABLED` | 112 | `true` |
| `DEFAULT_SOURCES` | 113 | `["memory"]` |

### Sync modes (lines 59–70, resolved at lines 228–250)

| Mode | Purpose |
|------|---------|
| `onSessionStart` | Sync when session starts |
| `onSearch` | Sync when search is triggered |
| `watch` | Watch filesystem for changes |
| `watchDebounceMs` | Debounce interval (default 1500ms) |
| `intervalMinutes` | Interval-based sync (0 = disabled) |
| `sessions.deltaBytes` | Sync when session size exceeds 100KB |
| `sessions.deltaMessages` | Sync when message count exceeds 50 |
| `sessions.postCompactionForce` | Force sync after compaction |

### Hybrid search execution (`extensions/memory-core/src/memory/hybrid.ts`)

```
query
 → vector search: vec_distance_cosine() on chunks table
 → keyword search: FTS5 BM25 ranking
 → score normalization: bm25RankToScore() (hybrid.ts:46–55)
 → hybrid merge: mergeHybridResults() (hybrid.ts:57–78)
     combined = vectorWeight × vectorScore + textWeight × textScore
 → optional: applyTemporalDecayToHybridResults()   (default disabled)
 → optional: applyMMRToHybridResults()              (default disabled)
 → filter: minScore, limit to maxResults
```

---

## 5. Session Memory Hook

Source: `src/hooks/bundled/session-memory/handler.ts`

### Trigger (lines 54–57)
```typescript
event.type === "command" && (event.action === "new" || event.action === "reset")
```

### Pipeline
```
/new or /reset command
 → resolve agent context                            lines 63–80
     agentId from session key (line 69)
     workspaceDir from context or ~/.openclaw/workspace (line 74)
     memoryDir = {workspaceDir}/memory (line 80)
 → extract messages                                  lines 127–132
     count: hookConfig?.messages or 15 (default)
 → generate filename slug                            lines 134–166
     generateSlugViaLLM() (line 156)
     fallback: HHMM timestamp (line 164)
 → write file                                        lines 169–207
     path: {memoryDir}/{YYYY-MM-DD}-{slug}.md
     content: # Session: {date} {time} UTC
              Session Key, Session ID, Source
              Conversation Summary (optional)
```

### Other files in hook directory
- `handler.test.ts` — comprehensive tests
- `transcript.ts` — transcript extraction helper
- `HOOK.md` — documentation

---

## 6. Embedding Providers

Source: `src/memory-host-sdk/host/embeddings.ts`

### Provider IDs (lines 44–53)

```typescript
type EmbeddingProviderId =
  | "openai" | "local" | "gemini" | "voyage"
  | "mistral" | "ollama" | "bedrock"
```

7 provider IDs. `"auto"` selects from remote providers: `["openai", "gemini", "voyage", "mistral"]` (line 59). Ollama excluded from auto (lines 56–57). Bedrock included when AWS credentials detected (line 58).

### Provider files (all in `src/memory-host-sdk/host/`)

| File | Provider |
|------|----------|
| `embeddings-openai.ts` | OpenAI API |
| `embeddings-gemini.ts` | Google Gemini API |
| `embeddings-mistral.ts` | Mistral API |
| `embeddings-voyage.ts` | Voyage AI API |
| `embeddings-ollama.ts` | Ollama (local HTTP) |
| `embeddings-bedrock.ts` | AWS Bedrock |
| `embeddings-debug.ts` | Debug/no-op |

Default local model (lines 96–97): `hf:ggml-org/embeddinggemma-300m-qat-q8_0-GGUF`

### Batch processing files (in `src/memory-host-sdk/host/`)
- `batch-openai.ts`, `batch-gemini.ts`, `batch-voyage.ts` — provider-specific batch APIs
- `batch-http.ts` — generic HTTP batch
- `batch-embedding-common.ts`, `batch-provider-common.ts` — shared batch logic
- `batch-runner.ts` — batch orchestration
- `batch-upload.ts`, `batch-output.ts`, `batch-status.ts` — file upload, result parsing, job tracking

### EmbeddingProvider interface (lines 35–42)
```typescript
type EmbeddingProvider = {
  id: string;
  model: string;
  maxInputTokens?: number;
  embedQuery: (text: string) => Promise<number[]>;
  embedBatch: (texts: string[]) => Promise<number[][]>;
  embedBatchInputs?: (inputs: EmbeddingInput[]) => Promise<number[][]>;
}
```

---

## 7. Per-Agent Store

### Path construction (`memory-search.ts:135–143`)

```typescript
function resolveStorePath(agentId: string, raw?: string): string {
  const stateDir = resolveStateDir(process.env, os.homedir);
  const fallback = path.join(stateDir, "memory", `${agentId}.sqlite`);
  // ...supports {agentId} token replacement in custom paths
}
```

### State directory resolution (`config/paths.ts:60–89`)

```
resolveStateDir()
 1. OPENCLAW_STATE_DIR env var                     line 65
 2. legacy .clawdbot dir if exists                 lines 73–84
 3. default: {homedir}/.openclaw                   line 88
```

Default per-agent path: `~/.openclaw/memory/{agentId}.sqlite`

Configurable via store path template with `{agentId}` token replacement (lines 140–142).

---

## 8. Indexing Pipeline

Source: `extensions/memory-core/src/memory/`

### Key components
- `index.ts` — main indexing orchestration
- `manager-reindex-state.ts` — incremental reindex tracking
- `manager-atomic-reindex.ts` — atomic reindex operations
- `manager-embedding-ops.ts` — embedding generation
- `manager-embedding-cache.ts` — embedding cache management
- `manager-embedding-policy.ts` — when to generate embeddings
- `manager-session-sync-state.ts` — session file change detection
- `manager-fts-state.ts` — FTS index state management

### Flow
```
file discovered (watch mode or manual sync)
 → hash-based change detection (files.hash column)
 → if changed:
     → chunk file (tokens: 400, overlap: 80)
     → generate embeddings (via configured provider)
     → check embedding cache (provider, model, provider_key, hash)
     → store: chunks table + FTS5 virtual table + optional cache
```

Incremental reindex via hash comparison. Full reindex on config changes.

---

## 9. QMD Query Engine

Source: `src/memory-host-sdk/host/qmd-*.ts`

### Components
- `qmd-process.ts` — CLI spawn execution (Windows-aware), binary availability check
- `qmd-query-parser.ts` — JSON result parsing (lines 16–49)
- `qmd-scope.ts` — scoping/context resolution

### Query result schema (`qmd-query-parser.ts`)
```typescript
type QmdQueryResult = {
  docid?: string;
  score?: number;
  collection?: string;
  file?: string;
  snippet?: string;
  body?: string;
  startLine?: number;
  endLine?: number;
}
```

### Score normalization (`hybrid.ts:46–55`)
```typescript
function bm25RankToScore(rank: number): number {
  if (rank < 0) {
    const relevance = -rank;
    return relevance / (1 + relevance);  // sigmoid-like
  }
  return 1 / (1 + rank);
}
```

---

## 10. Channels & Extensions

### Core channels (`src/channels/`)
125 non-test TypeScript files. Infrastructure code for:
- Chat type classification, metadata, state machine
- Typing indicators + lifecycle
- Draft message streaming
- Acknowledgment reactions
- Thread binding policy, command gating
- 30+ plugin orchestration files (`channels/plugins/`)

Channels are **independent of the memory system**. Memory integration happens via:
- `memory-core` extension (provides memory API to agents)
- `session-memory` hook (captures transcripts)

### Extensions (`extensions/`)
102 total directories. Memory-related:
- `memory-core/` — main memory implementation (indexing, search, hybrid merge)
- `memory-lancedb/` — LanceDB vector store backend (optional)
- `memory-wiki/` — wiki-style memory interface (optional)

Other categories: model providers (anthropic, google, openrouter, groq, etc.), search integrations (brave, duckduckgo, exa), chat channels (discord, matrix, irc, telegram), multimodal, observability.

---

## 11. Configuration

### Memory search config (`src/agents/memory-search.ts:16–93`)

`ResolvedMemorySearchConfig` type covers:
- `sources`: `"memory"` | `"sessions"` (+ `extraPaths`)
- `provider`: embedding provider ID
- `store`: driver (`"sqlite"`), path, FTS tokenizer, vector extension toggle
- `chunking`: `tokens` (400), `overlap` (80)
- `sync`: all sync mode settings
- `query`: `maxResults`, `minScore`, `hybrid` (weights, MMR, temporal decay)
- `cache`: `enabled`, `maxEntries`
- `remote`: optional remote embedding config (baseUrl, apiKey, batch settings)

### Config precedence (`memory-search.ts:145–150`)
1. Agent-specific: `config.agents[id].memorySearch`
2. Defaults: `config.agents.defaults.memorySearch`
3. Built-in constants (lines 97–113)

### Environment variables (`config/paths.ts`)

| Variable | Line | Purpose |
|----------|------|---------|
| `OPENCLAW_STATE_DIR` | 65 | Override state directory |
| `OPENCLAW_CONFIG_PATH` | 110 | Override config file path |
| `OPENCLAW_TEST_FAST` | 70 | Test mode flag |
| `OPENCLAW_NIX_MODE` | 15 | Nix environment detection |

---

## 12. Gap Analysis: openclaw vs co-cli

### openclaw has, co-cli does not

| Gap | openclaw | co-cli status | Severity |
|-----|----------|---------------|----------|
| **Three-phase dreaming** | Light (6h, dedup), Deep (daily, recency decay, health recovery), REM (weekly, pattern recognition). Configurable execution budget/speed/thinking (`dreaming.ts:7–145`) | co-cli has write-time agent-based upsert (`_save.py:64–100`). No background consolidation phases, no health-based recovery | **Medium** — co-cli's per-write dedup catches duplicates; openclaw's dreaming catches drift, patterns, and degraded memories over time |
| **Multi-provider embeddings** | 7 providers (openai, gemini, voyage, mistral, ollama, bedrock, local) with auto-selection, fallback chain, batch processing (`embeddings.ts`) | co-cli uses sqlite-vec for optional hybrid search (`_store.py:213`). No multi-provider embedding support | **Low** — co-cli's FTS5/BM25 is the primary search path; embeddings are supplementary |
| **Per-agent memory isolation** | Per-agent SQLite at `~/.openclaw/memory/{agentId}.sqlite` (`memory-search.ts:137`). Each agent has independent index, chunks, embeddings | co-cli is single-agent. One memory dir, one search index | **Not applicable** — co-cli is single-agent CLI |
| **Session transcript capture** | Session memory hook auto-saves last 15 messages on `/new`/`/reset` with LLM-generated slug (`handler.ts:54–207`) | co-cli has no automatic session transcript saving. Memory extraction is signal-based (`_extractor.py:141–225`), not transcript-based | **Low** — different approach. co-cli extracts signals (preferences, corrections); openclaw captures raw transcripts |
| **Hybrid search with configurable weights** | Vector 0.7 + text 0.3 (configurable), MMR dedup, temporal decay, candidate multiplier (`memory-search.ts:104–111`, `hybrid.ts`) | co-cli has FTS5/BM25 + optional sqlite-vec. Fixed scoring: 0.6×relevance + 0.4×decay (`memory.py:457–462`). No MMR | **Low** — co-cli's scoring is simpler but effective. openclaw's configurability is useful for tuning |
| **Embedding cache** | Per-provider embedding cache in SQLite (`memory-schema.ts:41–56`). Keyed by (provider, model, key, hash) | No embedding cache. FTS5 index rebuilt from file content | **Low** — cache reduces embedding API calls for unchanged content |

### co-cli has, openclaw does not

| Advantage | co-cli | openclaw status |
|-----------|--------|-----------------|
| **Rich typed frontmatter** | 14 validated fields (`_frontmatter.py:103–257`): provenance, certainty, auto_category, decay_protected, always_on, related | No memory-level metadata schema. Chunks have path, source, hash, model — no semantic classification |
| **Always-on standing context** | Up to 5 memories with `always_on=True` injected every turn (`agent.py:324–332`) | No always-on injection. Memory retrieved only via search |
| **Write-time dedup** | Agent-based upsert via `check_and_save()` (`_save.py:64–100`) before write | No write-time dedup. Dedup only during Light dreaming (0.9 similarity threshold) |
| **Automatic retention** | `enforce_retention()` (`_retention.py:16–52`) prunes oldest non-protected when > 200 | No retention mechanism. Memory grows with dreaming consolidation managing quality |
| **Cross-source knowledge search** | `search_knowledge` (`articles.py:161–309`) across memory, articles, Obsidian, Google Drive | Single-source: per-agent SQLite only. No cross-agent or external source search |
| **Explicit update tools** | `update_memory` (str_replace, `memory.py:830`) + `append_memory` (`memory.py:943`) with guards | No explicit memory update API. Dreaming handles quality refinement |
| **One-hop related traversal** | `related` frontmatter field + one-hop traversal in `recall_memory` (`memory.py:358–572`) | No relationship tracking between memories |
