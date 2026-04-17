# Co CLI — Knowledge: Reusable Artifacts & Retrieval

## Product Intent

**Goal:** Define the Knowledge layer — every reusable distilled artifact the agent should be able to recall across sessions: extracted insights, fetched articles, imported notes, and synced sources.

**Functional areas:**
- Unified knowledge artifact model (`KnowledgeArtifact`, `artifact_kind` subtypes)
- Artifact storage: human-editable `.md` files with YAML frontmatter in `knowledge_dir`
- FTS5 and hybrid (FTS5 + vector) retrieval via `KnowledgeStore`
- Two retrieval paths: turn-time recall, explicit tool search
- Extraction bridge from Memory (per-turn extractor writes to Knowledge)
- Consolidation lifecycle: dedup on write, dream cycle (merge, decay, transcript mining)

**Non-goals:**
- Raw episodic timeline storage — that is the Memory layer ([memory.md](memory.md))
- Media asset processing (audio/video ingest, transcription pipelines)
- Multi-user or concurrent-write safety
- Real-time file-watch triggers

**Success criteria:** All reusable recall routes through `search_knowledge()`; extracted insights and articles share one artifact model; retrieval uses turn-time recall and explicit search.

**Status:** Unified artifact model, single `knowledge_dir`, `source="knowledge"` indexing. Tool surface converged: `search_memories` delegates to `session_search`, `list_knowledge` is canonical, `/knowledge` is the primary REPL namespace with `/memory` as deprecated alias. Dedup-on-write, recall tracking, the full dream cycle (mine/merge/decay with archive/restore), and the `/knowledge stats` health dashboard are implemented; lifecycle machinery is gated behind `knowledge.consolidation_enabled` (default off).

**Known gaps:** None.

---

Knowledge is the reusable layer. Everything the agent should remember and apply across sessions — user preferences, project rules, fetched documentation, workflow feedback, external references — lives here as an individually-addressable, human-editable artifact. The boundary rule: **reusability defines the layer, not origin.** If an artifact is intended for reuse beyond its original session, it is Knowledge.

The two-layer model and the Memory / Knowledge boundary are defined in [cognition.md](cognition.md). This spec owns the implementation: artifact schema, storage model, retrieval paths, and lifecycle machinery.

## 1. What & How

Knowledge artifacts are stored as `.md` files with YAML frontmatter in `knowledge_dir`. A `KnowledgeStore` (SQLite FTS5 + optional vector) serves as the retrieval index. Disk is the source of truth; the DB is derived and rebuildable. Search queries always hit the DB — never read `.md` files at query time. `sync_dir()` keeps the DB current from disk.

```mermaid
flowchart TD
    subgraph Disk["knowledge_dir/*.md (source of truth)"]
        Artifacts["knowledge artifacts\n(YAML frontmatter + body)"]
        Archive["_archive/\n(archived artifacts)"]
    end

    subgraph DB["search.db (retrieval layer)"]
        Chunks["chunks + chunks_fts (FTS5)"]
        ChunksVec["chunks_vec_{dims} (optional vector)"]
    end

    subgraph Retrieval["Retrieval Paths"]
        TurnRecall["turn-time recall\n(inject_opening_context, top-N per turn)"]
        ExplicitSearch["search_knowledge()\n(on-demand, all artifact kinds)"]
    end

    subgraph Sources["Write Sources"]
        Extractor["per-turn extractor\n(fire-and-forget, detected signals)"]
        SaveArticle["save_article()\n(fetched docs, URL dedup)"]
        DreamCycle["dream cycle\n(batch mining, merge, decay)"]
        ManualWrite["manual / import\n(obsidian, drive, manual save)"]
    end

    Sources --> Artifacts
    Artifacts -->|sync_dir| DB
    DreamCycle -->|archive originals| Archive
    DB --> Retrieval
```

## 2. Core Logic

### 2.1 Knowledge Artifact Schema

Every knowledge artifact is a `.md` file with YAML frontmatter:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID4 string | Stable identity |
| `kind` | `knowledge` | Always `knowledge` for new artifacts |
| `artifact_kind` | enum | Semantic subtype (see below) |
| `title` | string | Human-readable label |
| `description` | string ≤200 chars | Compact summary for retrieval and manifests |
| `created` | ISO8601 | Creation timestamp |
| `updated` | ISO8601 | Last-modified timestamp |
| `tags` | list[str] | Retrieval and organization labels |
| `related` | list[str] | Soft links to related artifact slugs |
| `source_type` | enum | Origin: `detected`, `web_fetch`, `manual`, `obsidian`, `drive`, `consolidated` |
| `source_ref` | string | Pointer to origin: session ID, URL, file path, or artifact ID |
| `certainty` | enum | Confidence: `high`, `medium`, `low` |
| `decay_protected` | bool | Immune from automated decay |
| `last_recalled` | ISO8601 | Timestamp of most recent recall hit |
| `recall_count` | int | Count of recall hits |

**`artifact_kind` subtypes:**

| Kind | Meaning |
|------|---------|
| `preference` | User style, tool, or workflow preference |
| `feedback` | Correction or workflow guidance |
| `rule` | Project invariant or standing constraint |
| `decision` | Recorded design or architectural decision |
| `article` | Fetched external document or documentation |
| `reference` | External system pointer (URL, project, channel) |
| `note` | Manually authored or imported note |

The loader requires `kind: knowledge` with an `artifact_kind`; files missing either raise `ValueError`.

### 2.2 Storage Model

Knowledge uses a dual-layer storage model:

| Layer | What lives there | Purpose |
|-------|-----------------|---------|
| Disk (`knowledge_dir/*.md`) | Frontmatter + body | Source of truth. Human-editable, agent-readable. |
| DB (`search.db`) | FTS5 indexes, chunk tables, optional vector embeddings | Retrieval layer. Never read `.md` at query time. |

`sync_dir()` ingests `.md` files into `chunks` + `chunks_fts` tables (and optionally `chunks_vec_{dims}`) on bootstrap and after writes. The DB is fully rebuildable from disk.

Artifacts are split into overlapping chunks at index time (chunk size: 600 estimated tokens, overlap: 80). Extraction results (short artifacts < 2KB) become single-chunk documents. Articles remain multi-chunk.

### 2.3 Retrieval Paths

All reusable recall routes through the Knowledge layer via three paths:

**Turn-time recall** — on each new user turn, `inject_opening_context` calls `_recall_for_context()` which queries `search.db` for the top-3 knowledge artifacts matching the user's message. Results are injected as a trailing `SystemPromptPart`. Both extracted facts and articles are eligible — any reusable, relevant artifact surfaces here.

**Explicit search** — the agent calls `search_knowledge()` for on-demand retrieval. This is the universal reusable-recall surface: covers all artifact kinds plus Obsidian notes and Drive documents. Default `source=None` searches the unified knowledge layer.

**Search backends (three-tier fallback):**

| Backend | Mechanism | When used |
|---------|-----------|-----------|
| Hybrid | FTS5 + sqlite-vec vector similarity, merged via RRF (k=60) | Embedding provider available |
| FTS5 | BM25 over `chunks_fts`, porter/unicode61 tokenizer | Embedding provider unavailable |
| Grep | In-memory substring match over loaded `.md` files | `KnowledgeStore` unavailable |

**Confidence scoring** (applied to all search results):

```
confidence = 0.5 * score + 0.3 * decay + 0.2 * (provenance_weight * certainty_multiplier)

decay = exp(-ln(2) * age_days / half_life_days)
```

### 2.4 Knowledge Extraction (Memory → Knowledge Bridge)

The per-turn extractor is the primary path from Memory to Knowledge. It runs fire-and-forget after each clean turn (cadence-gated, default every 3 turns) and detects four signal categories:

| Signal | `artifact_kind` | Example |
|--------|-----------------|---------|
| User preference or profile | `preference` | "User prefers async/await over callbacks" |
| Correction or workflow guidance | `feedback` | "Don't mock the database in tests" |
| Project fact or standing rule | `rule` / `decision` | "Auth middleware rewrite is compliance-driven" |
| External system pointer | `reference` | "Pipeline bugs tracked in Linear INGEST" |

Extraction is always additive — the extractor writes new artifacts, never modifies or deletes existing ones. Dedup on write (token Jaccard similarity) prevents near-identical artifacts from accumulating when consolidation is enabled.

Full extraction flow is documented in [cognition.md §2.4](cognition.md).

### 2.5 Knowledge Lifecycle (Consolidation)

Batch lifecycle management via the dream cycle — runs at session end (when enabled) or via `/knowledge dream`. Three operations in sequence:

**Transcript mining** — reads recent sessions using a wider window, extracts cross-turn patterns the per-turn extractor may have missed.

**Knowledge merge** — groups artifacts by `artifact_kind`, computes pairwise similarity, consolidates clusters into higher-density artifacts. Originals are archived, never deleted.

**Decay sweep** — archives old artifacts with no recent recalls that are not decay-protected.

All archived artifacts are recoverable via `/knowledge restore`. Safety bounds and dream cycle mechanics are documented in [cognition.md §2.5](cognition.md).

### 2.6 REPL Commands

| Command | Purpose | Status |
|---------|---------|--------|
| `/knowledge list [query] [flags]` | List knowledge artifacts | Implemented |
| `/knowledge count [query] [flags]` | Count artifacts | Implemented |
| `/knowledge forget <query> [flags]` | Delete artifacts after confirm (removes file + DB row under `source="knowledge"`) | Implemented |
| `/knowledge dream [--dry]` | Run consolidation cycle manually | Implemented |
| `/knowledge restore [slug]` | List archived artifacts or restore by slug | Implemented |
| `/knowledge decay-review [--dry]` | Preview decay candidates, confirm to archive | Implemented |
| `/knowledge stats` | Health dashboard: counts by kind, decay-protected, archived, decay candidates, last dream | Implemented |

`/memory list|count|forget` remains as a deprecated alias that prints a deprecation notice and delegates to the `/knowledge` handlers.

## 3. Config

### Knowledge Settings

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | `grep`, `fts5`, or `hybrid` |
| `knowledge.embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `tei` | `tei`, `ollama`, `gemini`, or `none` |
| `knowledge.embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `embeddinggemma` | Embedding model name |
| `knowledge.embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` | `1024` | Embedding vector dimensions |
| `knowledge.embed_api_url` | `CO_KNOWLEDGE_EMBED_API_URL` | `http://127.0.0.1:8283` | Embedding service URL |
| `knowledge.cross_encoder_reranker_url` | `CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL` | `http://127.0.0.1:8282` | TEI reranker URL |
| `knowledge.chunk_size` | `CO_CLI_KNOWLEDGE_CHUNK_SIZE` | `600` | Chunk size (estimated tokens) |
| `knowledge.chunk_overlap` | `CO_CLI_KNOWLEDGE_CHUNK_OVERLAP` | `80` | Overlap between chunks |
| `knowledge.consolidation_enabled` | `CO_KNOWLEDGE_CONSOLIDATION_ENABLED` | `false` | Enable dream cycle and dedup-on-write |
| `knowledge.consolidation_trigger` | — | `session_end` | `session_end` or `manual` |
| `knowledge.consolidation_lookback_sessions` | — | `5` | Sessions to mine per dream cycle |
| `knowledge.consolidation_similarity_threshold` | — | `0.75` | Token Jaccard threshold for dedup/merge |
| `knowledge.max_artifact_count` | — | `300` | Soft cap — triggers decay review |
| `knowledge.decay_after_days` | `CO_KNOWLEDGE_DECAY_AFTER_DAYS` | `90` | Age threshold for decay candidacy |

### Injection Settings (in MemorySettings)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `memory.recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | Half-life for confidence decay scoring |
| `memory.injection_max_chars` | `CO_CLI_MEMORY_INJECTION_MAX_CHARS` | `2000` | Max chars for recalled knowledge injection |
| `memory.extract_every_n_turns` | `CO_CLI_MEMORY_EXTRACT_EVERY_N_TURNS` | `3` | Extraction cadence (0 = disabled) |

### Paths

| Path | Env Var | Default | Description |
|------|---------|---------|-------------|
| `knowledge_dir` | `CO_KNOWLEDGE_DIR` | `~/.co-cli/knowledge/` | All knowledge artifacts |
| `knowledge_db_path` | — | `~/.co-cli/co-cli-search.db` | FTS5/hybrid search index |

## 4. Files

### Knowledge Layer

| File | Purpose |
|------|---------|
| `co_cli/knowledge/_artifact.py` | `KnowledgeArtifact` dataclass, enums (`ArtifactKindEnum`, `SourceTypeEnum`, `CertaintyEnum`, `IndexSourceEnum`), loader |
| `co_cli/knowledge/mutator.py` | Atomic write helper (`_atomic_write`), unified FTS re-index (`_reindex_knowledge_file`), artifact body update (`_update_artifact_body`) |
| `co_cli/knowledge/_store.py` | `KnowledgeStore` — SQLite FTS5/hybrid search, `sync_dir()`, chunk indexing |
| `co_cli/knowledge/_frontmatter.py` | Frontmatter parse/validate, `render_knowledge_file`, `render_frontmatter` |
| `co_cli/knowledge/_chunker.py` | `chunk_text()` — paragraph/line/char split with overlap |
| `co_cli/knowledge/_ranking.py` | `compute_confidence()`, `detect_contradictions()` |
| `co_cli/knowledge/_embedder.py` | `build_embedder()` — dispatches to ollama/gemini/tei/none |
| `co_cli/knowledge/_reranker.py` | `build_llm_reranker()` — Ollama/Gemini listwise rerank |
| `co_cli/knowledge/_search_util.py` | Shared FTS query-build helpers |
| `co_cli/knowledge/_stopwords.py` | Shared `STOPWORDS` set for similarity and FTS tokenising |
| `co_cli/knowledge/_similarity.py` | `token_jaccard`, `find_similar_artifacts`, `is_content_superset` for dedup/merge |
| `co_cli/knowledge/_archive.py` | `archive_artifacts`, `restore_artifact` — move files to `_archive/` and back, keep FTS in sync |
| `co_cli/knowledge/_decay.py` | `find_decay_candidates` — age + recall filters with decay-protected immunity |
| `co_cli/knowledge/_dream.py` | `DreamState`, `DreamResult`, `run_dream_cycle`, `_mine_transcripts`, `_merge_similar_artifacts`, `_decay_sweep` |
| `co_cli/knowledge/prompts/dream_miner.md` | Retrospective transcript-miner sub-agent prompt |
| `co_cli/knowledge/prompts/dream_merge.md` | Consolidation-merge sub-agent prompt |
| `co_cli/tools/knowledge.py` | `save_knowledge()` (extractor-only), `list_knowledge()`, `search_knowledge()`, `save_article()` (writes `artifact_kind=article`), `search_articles()`, `read_article()`, `update_knowledge()`, `append_knowledge()`, internal helpers: `grep_recall`, `filter_artifacts`, `_recall_for_context`, `_touch_recalled`, `_find_by_slug` |
| `co_cli/tools/memory.py` | `search_memory()` — agent tool for episodic recall over session transcripts |

### Extraction & Injection

| File | Purpose |
|------|---------|
| `co_cli/knowledge/_distiller.py` | Fire-and-forget extraction pipeline, `build_transcript_window()`, `_tag_messages()` (shared with dream miner), cursor tracking |
| `co_cli/knowledge/prompts/knowledge_extractor.md` | Extractor sub-agent system prompt |
| `co_cli/main.py` | `_maybe_run_dream_cycle()` — session-end dream trigger gated by `consolidation_enabled` |
| `co_cli/context/_history.py` | `inject_opening_context` — per-turn knowledge recall into `SystemPromptPart` |
### Config

| File | Purpose |
|------|---------|
| `co_cli/config/_knowledge.py` | `KnowledgeSettings` — search, embedding, consolidation, decay |
| `co_cli/config/_memory.py` | `MemorySettings` — extraction cadence, injection limits |
