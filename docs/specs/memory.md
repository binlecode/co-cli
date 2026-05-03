# Co CLI — Memory

> Tool registration and approval: [tools.md](tools.md). Dream-cycle mining, merge, decay, archive: [dream.md](dream.md). Personality system and static prompt: [personality.md](personality.md). Prompt assembly: [prompt-assembly.md](prompt-assembly.md). Startup sequencing: [bootstrap.md](bootstrap.md). Turn orchestration: [core-loop.md](core-loop.md). Compaction mechanics: [compaction.md](compaction.md).

## 1. Architecture

Three-channel recall model. `memory_search()` dispatches all channels in sequence. Static personality content (soul seed, mindsets, rules) is injected once at agent construction — it is not a recall channel.

| Channel | Storage | Recall mechanism |
| --- | --- | --- |
| Sessions | `sessions/*.jsonl` → `co-cli-search.db` (`source='session'`) | BM25 chunk search → best chunk per unique session (dedup) → verbatim citations with JSONL line bounds; no LLM |
| Knowledge | `knowledge/*.md` → `co-cli-search.db` (`source='knowledge'`) | FTS5 BM25 ± RRF vector merge → optional reranker → ranked structured rows; no LLM by default |
| Canon | `souls/{role}/memories/*.md` → `co-cli-search.db` (`source='canon'`) | FTS5 BM25 → full body inline (no snippet truncation); indexed at bootstrap; no LLM |

`MemoryStore` is the shared search backend for all three channels. `memory_search()` in `co_cli/tools/memory/recall.py` dispatches all three channels.

```mermaid
flowchart TD
    subgraph Memory["Memory Layer"]
        Sessions["sessions/*.jsonl"]
    end

    subgraph Bridge["Bridge"]
        Dream["dream cycle (see dream.md)"]
    end

    subgraph Knowledge["Knowledge Layer"]
        KDir["knowledge/*.md"]
        CanonDir["souls/{role}/memories/*.md"]
        SearchDB["co-cli-search.db\n(chunks + FTS5 + optional vec)"]
    end

    subgraph Recall["Retrieval"]
        MemorySearch["memory_search()"]
    end

    Sessions -->|"index_session() → source='session'"| SearchDB
    Sessions --> Dream
    Dream --> KDir
    KDir -->|"sync_dir() → source='knowledge'"| SearchDB
    CanonDir -->|"sync_dir() → source='canon' (bootstrap)"| SearchDB
    SearchDB --> MemorySearch
```

## 2. Sessions Channel

### 2.1 Transcript Storage

Session transcripts are append-only JSONL files under `sessions_dir`:

```text
YYYY-MM-DD-THHMMSSZ-{uuid8}.jsonl
```

Each JSONL line is a message row serialized through `ModelMessagesTypeAdapter`.

`persist_session_history()` is the only transcript persistence primitive:

```text
if history_compacted:
    overwrite session_path with compacted messages (truncate + write)
else:
    append only messages[persisted_message_count:]
return session_path  # path never changes
```

Rules:
- Normal turns append a delta tail; compaction rewrites the file in place.
- Session path is stable for the lifetime of the conversation — no child sessions.
- `CoSessionState.persisted_message_count` is the only durability cursor.
- `load_transcript()` skips malformed lines and refuses to load files > 50 MB.
- `history_compacted` is sourced from `deps.runtime.compaction_applied_this_turn` by `_finalize_turn()` in `co_cli/main.py`; see compaction.md §2.3 for when `apply_compaction` sets it.

Oversized tool results appear as `<persisted-output>` placeholders in history (spilled to `tool-results/`); see compaction.md §2.1 M1 for thresholds, placeholder format, and per-tool overrides. Transcript files are `chmod 0o600`.

### 2.2 Lifecycle and Commands

Startup restore is path-only. `restore_session()` picks the latest `*.jsonl` by filename and sets `deps.session.session_path`; `_chat_loop()` begins with empty in-memory `message_history`. Resuming history is explicit.

| Command | Behavior |
| --- | --- |
| `/resume` | `list_sessions()` + interactive picker → `load_transcript(selected.path)`; adopts history and updates `session_path` |
| `/new` | Fresh `session_path` and clears in-memory history (prints "Nothing to rotate" if history empty) |
| `/clear` | Clears in-memory history; transcript files untouched |
| `/compact` | Run a compaction pass on current history — see compaction.md §1 manual entry for full behavior |
| `/sessions [keyword]` | Lists session summaries, optionally filtered by title substring |

### 2.3 Sessions Recall

`index_session()`:

```text
parse uuid8 and created_at from filename
chunk_session(path) → list[SessionChunk]
content_hash = sha256(joined chunk texts)
if hash unchanged: return  # hash-skip
with transaction:
    index doc row (source='session', path=uuid8, kind='session')
    index_chunks(source='session', doc_path=uuid8, chunks)
```

`session_chunker.py` pipeline:
- `extract_messages(path)` → parses JSONL, skips control lines and noise parts
- `flatten_session(messages)` → role-prefixed lines: `User:`, `Assistant:`, `Tool[name](call):`, `Tool[name](return):`
- `chunk_flattened(flat_lines, line_map)` → sliding-window token chunks, each with `start_jsonl_line` / `end_jsonl_line`

`init_session_index()` runs at bootstrap. On first run after migration it removes the obsolete `session-index.db` if present.

`memory_search()` modes:
- **Browse** (empty query): returns recent-session metadata — ID, date, title, file size — no FTS, no LLM. Excludes the current session.
- **Search** (keyword query): sessions channel → `MemoryStore.search(sources=['session'], limit=15)` → dedup to one best chunk per unique session → cap at 3 (`_SESSIONS_CHANNEL_CAP`)

Result shape: `{channel: "sessions", session_id, when, source, chunk_text, start_line, end_line, score}`

Source includes `memory_read_session_turn(session_id, start_line, end_line)` as a capped JSONL line-range reader, but it is not currently registered in the foreground native toolset. Registered `memory_search` results therefore expose session snippets and line bounds; exact follow-up reads require source wiring before they are model-callable.

The active session is excluded from bootstrap sync; episodic search covers already-indexed transcripts only.

## 3. Knowledge Channel

### 3.1 Artifact Storage

Knowledge artifacts are reusable facts the agent recalls across sessions: `user`, `rule`, `article`, and `note` kinds.

| Layer | What lives there | Purpose |
| --- | --- | --- |
| `knowledge_dir/*.md` | YAML frontmatter + body text | Source of truth; human-editable |
| `co-cli-search.db` | `chunks`, `chunks_fts`, optional `chunks_vec`, `docs` | Derived retrieval layer |

`sync_dir()` keeps the DB current: parses frontmatter, SHA256 hash-skips unchanged files, chunks body text, and writes to `chunks`/`chunks_fts`. Obsidian and Drive connectors index under `source='obsidian'`/`source='drive'`.

Knowledge artifact schema:

| Field | Purpose |
| --- | --- |
| `id` | Stable UUID |
| `artifact_kind` | `user`, `rule`, `article`, or `note` |
| `title` | Human-readable label |
| `description` | Short retrieval summary |
| `created` | ISO8601 creation timestamp |
| `updated` | ISO8601 last-modified timestamp |
| `related` | Soft links to related artifacts |
| `source_type` | `detected`, `web_fetch`, `manual`, `obsidian`, `drive`, or `consolidated` |
| `source_ref` | Pointer to source session, URL, file path, or artifact ID |
| `decay_protected` | Lifecycle protection flag; decay semantics in [dream.md](dream.md) |
| `last_recalled` | Most recent recall timestamp |
| `recall_count` | Recall hit counter |

### 3.2 Artifact Recall

RAG pipeline:

```text
MemoryStore.search(query, sources=['knowledge']):
    fts_chunks = FTS5 BM25 over chunks_fts
    if hybrid:
        vec_chunks = cosine search over chunks_vec
        merged = RRF(fts_chunks, vec_chunks)   # k=60
    else:
        merged = fts_chunks
    return _rerank_results(query, merged, limit)
```

Backend resolution:

| Backend | Mechanism | When used |
| --- | --- | --- |
| `hybrid` | FTS5 BM25 + sqlite-vec cosine, RRF merge | Configured, TEI reranker reachable, embedding provider configured/reachable, and sqlite-vec available |
| `fts5` | BM25 over chunked text only | Explicitly configured, or hybrid degrades before store construction |

When `memory_store` is `None` (set when `search_backend="grep"` or store init fails), `_search_artifacts` falls back to `grep_recall` — in-memory substring match over artifact title and content. The sessions channel returns `[]` in this state.

Optional reranker (applied after merge, before limit): TEI cross-encoder (`cross_encoder_reranker_url`); unconfigured = pass-through.

Result shape: `{channel: "artifacts", kind, title, snippet, score, path, filename_stem}`

Full body requires a follow-up `file_read` on `path`.

Knowledge commands:

| Command | Purpose |
| --- | --- |
| `/memory list [query] [flags]` | List matching artifacts |
| `/memory count [query] [flags]` | Count matching artifacts |
| `/memory forget <query> [flags]` | Delete matching active artifacts after confirmation |

Dream lifecycle commands (`/memory dream`, `/memory restore`, `/memory decay-review`, `/memory stats`) live in [dream.md](dream.md).

### 3.3 Artifact Write Paths

Two write tools:

1. **`memory_create`** — dispatched through `save_artifact()`:
   - `source_url` set → URL-keyed dedup (web articles); `decay_protected` forced True
   - `consolidation_enabled` → Jaccard dedup; >0.9 near-identical skipped, overlapping merged
   - else → straight create

2. **`memory_modify`** — append content or surgically replace a passage. Guards: rejects Read-tool line-number prefixes; for `replace`, target must appear exactly once.

Writes use `atomic_write()` (temp-file + `os.replace`). `reindex()` is called at the tool layer with config-sourced `chunk_size`/`chunk_overlap` — not inline in the write functions.

Archive/restore: `archive_artifacts()` moves files to `knowledge_dir/_archive/` and removes them from the index; `restore_artifact()` moves them back and re-indexes. The `_archive/` subdir is never traversed by default loaders.

## 4. Canon Channel

Canon files (`souls/{role}/memories/*.md`) are package-shipped and read-only. They are intentionally excluded from static prompt injection — a scene either matches the moment or it doesn't, and static injection pays full token cost whether it lands or not. Canon is served on demand via `memory_search`.

**Indexing (bootstrap-time):** `_sync_canon_store()` in `co_cli/bootstrap/core.py` runs after knowledge sync. It calls `store.sync_dir("canon", canon_dir, glob="*.md", no_chunk=True)`. The `no_chunk=True` flag stores each file as a single `Chunk(index=0)` — no splitting — because canon scenes are small (<1KB) and must be returned whole. Hash-skip, stale eviction, and frontmatter parsing work identically to other sources. No-ops when `store is None` or `config.personality` is empty.

**Recall:** `_search_canon_channel()` calls `store.search(query, sources=["canon"], limit=character_recall_limit)`, then fetches the full body with `store.get_chunk_content("canon", path, 0)` — direct `SELECT content FROM chunks` lookup. The FTS5 `snippet()` function is never used for canon; the stored full-body chunk is returned as-is. Returns `[]` when `memory_store is None` or `personality` is empty.

Result shape: `{channel: "canon", role, title, body, score}` — full body inline, no follow-up needed.

## 5. Full Recall Path

```
memory_search(ctx, query, kinds, limit)             # tools/memory/recall.py
  ├─ _search_artifacts(ctx, query, kinds, limit)
  │    ├─ [store available]
  │    │    MemoryStore.search(query, sources=['knowledge'], kinds, limit)
  │    │      ├─ FTS5 BM25 over chunks_fts
  │    │      ├─ [hybrid] embed(query) → cosine over chunks_vec → RRF merge (k=60)
  │    │      └─ _rerank_results(query, merged, limit)
  │    │           └─ [tei] cross-encoder HTTP rerank
  │    └─ [store unavailable]
  │         load_knowledge_artifacts() → grep_recall()     # in-memory substring fallback
  │
  ├─ _search_sessions(ctx, query, span)
  │    └─ MemoryStore.search(query, sources=['session'], limit=15)
  │         └─ dedup to 3 unique sessions (_SESSIONS_CHANNEL_CAP); excludes current session
  │
  └─ _search_canon_channel(ctx, query)
       └─ [store available + personality set]
            store.search(query, sources=['canon'], limit)     # FTS5 BM25
            for each hit: store.get_chunk_content('canon', path, 0)  → full body
       └─ [store unavailable or personality empty] → []

  └─ merge channels → format and return flat result list
```

All three channels run in sequence. Results carry a `channel` field (`artifacts`, `sessions`, `canon`); scores are not cross-comparable across channels.

## 6. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | preferred retrieval backend before runtime degradation |
| `knowledge.embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `tei` | embedding backend (`ollama`, `gemini`, `tei`, `none`) |
| `knowledge.embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `embeddinggemma` | embedding model name |
| `knowledge.embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` | `1024` | embedding vector dimensions |
| `knowledge.embed_api_url` | `CO_KNOWLEDGE_EMBED_API_URL` | `http://127.0.0.1:8283` | embedding service URL |
| `knowledge.cross_encoder_reranker_url` | `CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL` | `http://127.0.0.1:8282` | TEI cross-encoder reranker URL |
| `knowledge.tei_rerank_batch_size` | *(no env var)* | `50` | batch size for TEI rerank HTTP requests |
| `knowledge.chunk_size` | `CO_KNOWLEDGE_CHUNK_SIZE` | `600` | artifact chunk size in chars during indexing |
| `knowledge.chunk_overlap` | `CO_KNOWLEDGE_CHUNK_OVERLAP` | `80` | artifact chunk overlap in chars |
| `knowledge.session_chunk_tokens` | `CO_KNOWLEDGE_SESSION_CHUNK_TOKENS` | `400` | session chunk size in tokens |
| `knowledge.session_chunk_overlap` | `CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP` | `80` | session chunk overlap in tokens |
| `knowledge.consolidation_enabled` | `CO_KNOWLEDGE_CONSOLIDATION_ENABLED` | `false` | enable Jaccard dedup on artifact writes |
| `knowledge.consolidation_trigger` | *(no env var)* | `session_end` | when consolidation runs: `session_end` or `manual` |
| `knowledge.consolidation_lookback_sessions` | *(no env var)* | `5` | past sessions to mine during consolidation |
| `knowledge.consolidation_similarity_threshold` | *(no env var)* | `0.75` | Jaccard score threshold for artifact dedup/merge |
| `knowledge.max_artifact_count` | *(no env var)* | `300` | soft cap on total artifact count |
| `knowledge.decay_after_days` | `CO_KNOWLEDGE_DECAY_AFTER_DAYS` | `90` | days before decay eligibility |
| `knowledge.character_recall_limit` | `CO_KNOWLEDGE_CHARACTER_RECALL_LIMIT` | `3` | max canon hits per `memory_search` call (legacy alias: `CO_CHARACTER_RECALL_LIMIT`) |
| `memory.recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | defined lifecycle setting; not currently consumed by recall ranking |

Dream-cycle and lifecycle maintenance settings live in [dream.md](dream.md).

### Paths

| Path | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge_path` | `CO_KNOWLEDGE_PATH` | `~/.co-cli/knowledge/` | knowledge artifact source-of-truth directory |
| `sessions_dir` | — | `~/.co-cli/sessions/` | transcript directory |
| `tool_results_dir` | — | `~/.co-cli/tool-results/` | spill directory for oversized tool results |
| `memory_db_path` | — | `~/.co-cli/co-cli-search.db` | unified retrieval DB (sessions + knowledge + canon) |

## 7. Files

### Sessions

| File | Purpose |
| --- | --- |
| `co_cli/memory/session.py` | session filename parsing, generation, latest-session discovery |
| `co_cli/memory/transcript.py` | transcript append/load, child-session branching, control records |
| `co_cli/memory/session_browser.py` | session listing and picker metadata for `/resume` and `/sessions` |
| `co_cli/memory/session_chunker.py` | chunking pipeline: `flatten_session()`, `chunk_flattened()`, `chunk_session()` |
| `co_cli/memory/indexer.py` | JSONL line parser: `ExtractedMessage`, `extract_messages()` |
| `co_cli/tools/memory/read.py` | `grep_recall()` — artifact title/content substring fallback; `memory_read_session_turn()` — verbatim JSONL turn reader |
| `co_cli/tools/tool_io.py` | oversized tool-result spill, preview placeholders, size warnings |
| `co_cli/bootstrap/core.py` | `restore_session()`, `init_session_index()` — startup bootstrap |
| `co_cli/main.py` | `_finalize_turn()` — session persistence bridge and session-end dream trigger |
| `co_cli/commands/core.py` | slash-command registry and dispatch |
| `co_cli/commands/resume.py` | `/resume` command handler |
| `co_cli/commands/new.py` | `/new` command handler |
| `co_cli/commands/clear.py` | `/clear` command handler |
| `co_cli/commands/compact.py` | `/compact` command handler |
| `co_cli/commands/sessions.py` | `/sessions` command handler |

### Knowledge

| File | Purpose |
| --- | --- |
| `co_cli/memory/memory_store.py` | `MemoryStore` — FTS5/hybrid search, `sync_dir()`, `index_session()`, `sync_sessions()` |
| `co_cli/memory/artifact.py` | `KnowledgeArtifact` schema, kind enums, artifact loaders |
| `co_cli/memory/service.py` | pure-function write layer: `save_artifact()`, `mutate_artifact()` |
| `co_cli/memory/_mutator.py` | `atomic_write()` — temp-file + `os.replace` write helper |
| `co_cli/memory/archive.py` | `archive_artifacts()`, `restore_artifact()` |
| `co_cli/memory/text_chunker.py` | knowledge artifact text chunking |
| `co_cli/memory/frontmatter.py` | frontmatter parse, validate, render |
| `co_cli/memory/similarity.py` | Jaccard similarity and content-superset helpers |
| `co_cli/memory/search_util.py` | `normalize_bm25()`, `run_fts()`, `sanitize_fts5_query()`, `snippet_around()` |
| `co_cli/memory/_embedder.py` | `build_embedder()` — embedding provider dispatch |
| `co_cli/memory/stopwords.py` | `STOPWORDS` frozenset |
| `co_cli/memory/decay.py` | artifact decay scoring and eligibility |
| `co_cli/memory/dream.py` | dream-cycle orchestration (see [dream.md](dream.md)) |
| `co_cli/tools/memory/recall.py` | `memory_search()` — unified recall tool |
| `co_cli/tools/memory/write.py` | `memory_create()`, `memory_modify()` |
| `co_cli/commands/knowledge.py` | `/memory` command family handler |

### Canon

| File | Purpose |
| --- | --- |
| `co_cli/bootstrap/core.py` | `_sync_canon_store()` — indexes canon scenes into FTS at bootstrap |
| `co_cli/memory/memory_store.py` | `sync_dir(no_chunk=True)`, `get_chunk_content()` — canon indexing and full-body fetch |
| `co_cli/tools/memory/recall.py` | `_search_canon_channel()` — BM25 recall over `source='canon'` |
| `co_cli/context/assembly.py` | `build_static_instructions()` — static prompt assembly (canon explicitly excluded here) |

## 8. Test Gates

| Property | Test file |
| --- | --- |
| FTS5 search finds an indexed artifact entry | `tests/test_flow_memory_search.py` |
| `mutate_artifact` replace preserves frontmatter | `tests/test_flow_memory_lifecycle.py` |
| `mutate_artifact` append adds to body | `tests/test_flow_memory_lifecycle.py` |
| Session restore picks the most recent transcript | `tests/test_flow_bootstrap_session.py` |
| `grep_recall` returns artifact matched by title only | `tests/test_flow_memory_recall.py` |
| `_list_artifacts` delegates to index when store is available | `tests/test_flow_memory_recall.py` |
| `save_artifact` URL dedup uses O(1) index when `memory_store` set | `tests/test_flow_memory_write.py` |
| `sync_dir(no_chunk=True)` stores one chunk per file; `get_chunk_content()` returns full body; hash-skip on rerun | `tests/test_flow_memory_store_nochunk.py` |
| `_sync_canon_store()` indexes real canon files; no-ops on `store=None` / `personality=None` | `tests/test_flow_bootstrap_canon.py` |
| `_search_canon_channel()` returns full body; returns `[]` on no store or no personality | `tests/test_flow_canon_recall.py` |
