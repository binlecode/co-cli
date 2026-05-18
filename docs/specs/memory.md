# Co CLI — Memory

> Peer tier: [sessions.md](sessions.md) (past conversation transcripts). Sibling surfaces: [skills.md](skills.md) (procedural capability). Doctrine (auto-injected into static prompt; never queried as memory): [personality.md](personality.md). Tool registration and approval: [tools.md](tools.md). Dream-cycle mining, merge, decay, archive: [dream.md](dream.md). Prompt assembly: [prompt-assembly.md](prompt-assembly.md). Startup sequencing: [bootstrap.md](bootstrap.md). Turn orchestration: [core-loop.md](core-loop.md).

Foundation spec for the memory tier — long-term declarative artifacts (user preferences, rules, articles, notes) that the agent accumulates and recalls.

Memory is one of five operational tiers in the agent loop: **doctrine** ([personality.md](personality.md), identity), **tools** ([tools.md](tools.md), capability), **skills** ([skills.md](skills.md), procedure), **memory** (this file — long-term declarative artifacts), and **session** ([sessions.md](sessions.md) — past conversation transcripts). Memory and session are peer tiers sharing the same index infrastructure but with distinct domain logic, mutation models, and lifecycle policies.

## 1. Functional Architecture

Memory holds long-term declarative artifacts: facts the agent has accumulated and that should outlive a single conversation. It is mutable (CRUD via `memory_manage`), kind-typed (user / rule / article / note), and subject to lifecycle (decay, dream consolidation).

Memory is never injected wholesale into the system prompt. Static personality content (soul seed, mindsets, bundled skill manifest) is injected once at agent construction. Memory artifacts are loaded on-demand through the recall tool surface, keeping context bounded and recall purposeful.

```mermaid
flowchart TD
    MemoryFiles["memory artifacts\n(~/.co-cli/memory/*.md)"] -->|"source='memory'"| IndexDB["co-cli-search.db\n(IndexStore + FTS5 + optional vec)"]
    IndexDB --> MemSearch["memory_search()"]
    IndexDB --> MemView["memory_view()"]
    MemManage["memory_manage()"] --> MemoryFiles
```

### Tier ontology

| Tier | Storage | Mutation | Indexing |
| --- | --- | --- | --- |
| **memory** (this spec) | `~/.co-cli/memory/*.md` | `memory_manage(action=create/append/replace/delete)` | FTS5 BM25 + optional hybrid; paragraph-aware chunking |
| **session** ([sessions.md](sessions.md)) | `~/.co-cli/sessions/*.jsonl` | append-only via `persist_session_history` | sliding-window token chunks |

Canon scenes (`souls/{role}/canon/*.md`) coexist in the same DB under `source='canon'` for personality auto-injection, but are never returned by any model-callable tool. Skills live on their own tier.

## 2. Architecture layers

```
co_cli/tools/memory/     Agent surface — memory_search, memory_view, memory_manage
        ↓
co_cli/memory/           Domain — MemoryStore (kinds, decay, dream, two-pass search)
        ↓
co_cli/index/            Infrastructure facade — IndexStore (public) + retrieval, embedding, providers (private)
        ↓
                         SQLite + FTS5 + optional sqlite-vec
```

`IndexStore` is the only public class in `co_cli/index/`. `RetrievalService` (FTS + vec + RRF + rerank), `EmbeddingService` (embed + cache), and provider dispatch are private submodules — domain modules never import them directly.

### Retrieval backends

| Backend | Mechanism | When used |
| --- | --- | --- |
| `hybrid` | FTS5 BM25 + sqlite-vec cosine, RRF merge (k=60) | Configured, TEI reranker reachable, embedding provider configured/reachable, and sqlite-vec available |
| `fts5` | BM25 over chunked text only | Explicitly configured, or hybrid degrades before store construction |
| `grep` | In-memory substring over artifact title+content | `memory_store` is `None`; sessions return `[]` in this state |

Optional reranker (applied after merge, before limit): TEI cross-encoder (`cross_encoder_reranker_url`); unconfigured = pass-through.

## 3. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `memory.search_backend` | `CO_MEMORY_SEARCH_BACKEND` | `hybrid` | preferred retrieval backend before runtime degradation |
| `memory.embedding_provider` | `CO_MEMORY_EMBEDDING_PROVIDER` | `tei` | embedding backend (`ollama`, `gemini`, `tei`, `none`) |
| `memory.embedding_model` | `CO_MEMORY_EMBEDDING_MODEL` | `embeddinggemma` | embedding model name |
| `memory.embedding_dims` | `CO_MEMORY_EMBEDDING_DIMS` | `1024` | embedding vector dimensions |
| `memory.embed_api_url` | `CO_MEMORY_EMBED_API_URL` | `http://127.0.0.1:8283` | embedding service URL |
| `memory.cross_encoder_reranker_url` | `CO_MEMORY_CROSS_ENCODER_RERANKER_URL` | `http://127.0.0.1:8282` | TEI cross-encoder reranker URL |
| `memory.tei_rerank_batch_size` | *(no env var)* | `50` | batch size for TEI rerank HTTP requests |
| `memory.chunk_tokens` | `CO_MEMORY_CHUNK_TOKENS` | `600` | paragraph-aware chunking budget for memory artifacts |
| `memory.chunk_overlap_tokens` | `CO_MEMORY_CHUNK_OVERLAP_TOKENS` | `80` | chunk overlap |
| `memory.consolidation_enabled` | `CO_MEMORY_CONSOLIDATION_ENABLED` | `false` | dream-cycle consolidation |
| `memory.recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | lifecycle setting; not currently consumed by recall ranking |

Session chunking settings live alongside memory settings under `config.memory.session_chunk_*` since both tiers share the index infrastructure. See [sessions.md](sessions.md) for session-specific config.

### Paths

| Path | Env Var | Default | Description |
| --- | --- | --- | --- |
| `memory_path` | `CO_MEMORY_PATH` | `~/.co-cli/memory/` | memory artifact source-of-truth directory |
| `sessions_dir` | — | `~/.co-cli/sessions/` | transcript directory |
| `tool_results_dir` | — | `~/.co-cli/tool-results/` | spill directory for oversized tool results |
| `memory_db_path` | — | `~/.co-cli/co-cli-search.db` | unified retrieval DB (memory + session + canon) |

## 4. Public Interface

### Recall and view

| Symbol | Source | Contract |
| --- | --- | --- |
| `memory_search(ctx, query, kinds=None, limit=10)` | `co_cli/tools/memory/recall.py` | Async tool — two-pass ranked recall over memory artifacts; empty query → recent-artifact browse |
| `memory_view(ctx, name)` | `co_cli/tools/memory/view.py` | Async tool — returns full artifact body by `filename_stem`; frontmatter stripped |

Result fields for `memory_search`: `{kind, title, snippet, score, path, filename_stem}`. Two-pass policy in `co_cli/memory/store.py`: user-kind priority pass (cap `_USER_PRIORITY_CAP=3`) + waterfall over remaining kinds (cap `_WATERFALL_CHUNK_CAP=5`).

### Write

| Symbol | Source | Contract |
| --- | --- | --- |
| `memory_manage(ctx, action, name, content=None, kind=None, section=None)` | `co_cli/tools/memory/manage.py` | Async tool — `create`/`append`/`replace`/`delete`; `approval=True`; subject `tool:memory_manage:<action>:<name>` |

### Domain API

| Symbol | Source | Contract |
| --- | --- | --- |
| `MemoryStore(index, config)` | `co_cli/memory/store.py` | Domain store composing IndexStore — owns memory kinds, two-pass search, decay hooks |
| `MemoryStore.sync_dir(memory_dir)` | `co_cli/memory/store.py` | Hash-based directory indexer for memory artifacts |
| `MemoryStore.search_artifacts(query, kinds, limit)` | `co_cli/memory/store.py` | Two-pass FTS recall with user-kind priority + kind waterfall |
| `MemoryStore.list_artifacts(kinds, limit)` | `co_cli/memory/store.py` | Inventory rows for browse mode |
| `MemoryArtifact` | `co_cli/memory/artifact.py` | Reusable artifact data model |
| `ArtifactKindEnum` | `co_cli/memory/artifact.py` | USER / RULE / ARTICLE / NOTE (and CANON for the doctrine source) |
| `save_artifact`, `mutate_artifact` | `co_cli/memory/service.py` | Pure write functions — no RunContext |

### Index API (cross-tier)

| Symbol | Source | Contract |
| --- | --- | --- |
| `IndexStore` | `co_cli/index/store.py` | Infrastructure facade — schema, write CRUD, transactions, search facade |
| `IndexStore.search(query, sources, kinds, limit)` | `co_cli/index/store.py` | Delegates to private RetrievalService — returns `SearchResult` rows |
| `IndexStore.upsert(...)`, `index_chunks(source, doc_path, chunks)` | `co_cli/index/store.py` | Source-agnostic write CRUD |
| `IndexStore.remove(source, path)`, `remove_stale(source, current_paths)` | `co_cli/index/store.py` | Source-agnostic deletion |
| `Chunk` | `co_cli/index/chunk.py` | Write contract — `(index, content, start_line, end_line)` |
| `SearchResult` | `co_cli/index/_retrieval.py` | Ranked result row |

## 5. Files

### Infrastructure (`co_cli/index/`)

| File | Purpose |
| --- | --- |
| `co_cli/index/store.py` | `IndexStore` — schema, CRUD, transactions, search facade |
| `co_cli/index/chunk.py` | `Chunk` dataclass — write contract |
| `co_cli/index/schema.py` | DDL constants |
| `co_cli/index/_retrieval.py` | `RetrievalService` — FTS + vec + RRF + rerank (private) |
| `co_cli/index/_embedding.py` | `EmbeddingService` — embed + cache (private) |
| `co_cli/index/_providers.py` | ollama / tei / gemini dispatch (private) |
| `co_cli/index/_search_util.py` | FTS5 query sanitize, BM25 normalize, snippet helpers (private) |
| `co_cli/index/_stopwords.py` | `STOPWORDS` frozenset (private) |

### Memory domain (`co_cli/memory/`)

| File | Purpose |
| --- | --- |
| `co_cli/memory/store.py` | `MemoryStore` — domain store composing IndexStore |
| `co_cli/memory/service.py` | `save_artifact`, `mutate_artifact`, `reindex` |
| `co_cli/memory/chunker.py` | `chunk_text` — paragraph-aware chunking |
| `co_cli/memory/artifact.py` | `MemoryArtifact`, `ArtifactKindEnum`, `IndexSourceEnum` |
| `co_cli/memory/frontmatter.py` | YAML frontmatter parse/render |
| `co_cli/memory/similarity.py` | Jaccard dedup for consolidation |
| `co_cli/memory/decay.py` | Decay candidate identification |
| `co_cli/memory/dream.py` | Dream-cycle orchestration |
| `co_cli/memory/archive.py` | Archive / restore artifact files |

### Tool surface (`co_cli/tools/memory/`)

| File | Purpose |
| --- | --- |
| `co_cli/tools/memory/recall.py` | `memory_search` — ranked recall |
| `co_cli/tools/memory/view.py` | `memory_view` — full artifact body reader |
| `co_cli/tools/memory/manage.py` | `memory_manage` — write surface |
