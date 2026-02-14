# TODO: SQLite FTS + Semantic Search

**Status:** Backlog
**Scope:** All text sources co-cli touches — knowledge files (memories, articles), Obsidian notes, Google Drive docs
**Reference:** [OpenClaw memory system](~/workspace_genai/openclaw/src/memory/), [TODO-knowledge-articles.md](TODO-knowledge-articles.md)

## Problem

Every search in co-cli is naive. `recall_memory()` uses grep with recency-only sorting. `search_notes()` walks the filesystem with regex. `search_drive_files()` relies on the API's `fullText` query. None of them rank results by relevance, and there's no way to search across sources.

## Design Principle

`KnowledgeIndex` is a single SQLite-backed search index (`search.db`) that any source can write to. The `source` column (`'memory'`, `'article'`, `'obsidian'`, `'drive'`) distinguishes origin. Tools index text opportunistically — you can only index what you have. External sources (Drive docs, Obsidian notes) get indexed when tools read them; there is no background crawler.

| Source | Index trigger | Chunking | Notes |
|--------|--------------|----------|-------|
| Memory | `save_memory()` + startup sync | Whole-file (small) | Frontmatter indexed (tags, category) |
| Article | `save_article()` + startup sync | Whole-file or section | Frontmatter indexed |
| Obsidian | On `search_notes()` first call, mtime-based incremental | Whole-file | Local markdown, high benefit |
| Drive | On `search_drive_files()` when doc text is fetched | Whole-file | Cached locally, re-sync if stale |

---

## OpenClaw Reference

OpenClaw's memory system (`openclaw/src/memory/`) is a production-grade hybrid search pipeline. Key patterns worth adopting:

### What they do well

1. **Hybrid merge with tunable weights** — FTS5 (BM25) + sqlite-vec (cosine), merged via weighted score combination (default 70% vector / 30% keyword). Simple and effective.

2. **Normalized scoring** — BM25 rank converted to [0,1] via `1 / (1 + rank)`, cosine distance converted via `1 - distance`. Allows meaningful score comparison across retrieval methods.

3. **Embedding cache** — Dedup table keyed on `(provider, model, hash)` avoids re-embedding identical text. Critical when memory content is stable.

4. **FTS5 query building** — Tokenizes raw query, AND-joins quoted terms. Simple, predictable, avoids FTS5 syntax errors from user input.

5. **Graceful degradation** — If sqlite-vec extension unavailable, falls back to in-memory cosine similarity. If embedding provider fails, falls back to FTS5-only.

6. **Source filtering** — SQL WHERE clause on `source` column enables scoped queries (memories-only, sessions-only, or all).

### What we should do differently

1. **No chunking for memories** — OpenClaw chunks at 400 tokens with 80-token overlap because their memory files can be large. Our memories are single-paragraph items (~50-200 tokens). Index whole files, not chunks. Articles may need chunking later — defer until articles land.

2. **Simpler schema** — OpenClaw stores chunks with line numbers, model identifiers, and embedding text in a single `chunks` table. We already have frontmatter metadata (tags, source, category, decay_protected) that should be first-class indexed fields, not just payload.

3. **Frontmatter-aware search** — OpenClaw has no structured metadata filtering. We should support tag-scoped and category-scoped search (e.g., `recall_memory("pytest", tags=["preference"])`) by indexing frontmatter fields as FTS5 columns or SQL WHERE filters.

4. **Markdown files remain source of truth** — OpenClaw's SQLite is the sole authority. Our markdown files are the source of truth; the SQLite index is derived and rebuildable. On startup, sync index from files (hash-based change detection, like OpenClaw's `files` table).

---

## Architecture

```
recall_memory(query)  /  search_notes(query)  /  search_knowledge(query)
       │                         │                         │
       ▼                         ▼                         ▼
  KnowledgeIndex.search(query, source="memory"|"obsidian"|None, tags?, limit)
       │
       ├── Phase 1: FTS5 MATCH + bm25() → ranked results
       │
       ├── Phase 2: + vec0 cosine similarity → hybrid merge
       │
       └── (Phase 3: + cross-encoder rerank)
       │
  ┌────┴────┐
  │ search.db │  ~/.local/share/co-cli/search.db
  └─────────┘
```

**`KnowledgeIndex`** is the single search class. All sources write to the same `docs` table via `index()`. Source-specific tools (`recall_memory`, `search_notes`, `search_drive_files`) pass `source=` to scope queries. `search_knowledge(query)` searches all sources.

---

## Schema

```sql
CREATE TABLE IF NOT EXISTS docs (
    source  TEXT NOT NULL,           -- 'memory', 'article', 'obsidian', 'drive', ...
    path    TEXT NOT NULL,           -- relative path within .co-cli/knowledge/
    title   TEXT,                    -- memory slug or article title
    content TEXT,                    -- full markdown body (no frontmatter)
    mtime   REAL,                    -- file mtime for change detection
    hash    TEXT,                    -- SHA256 of file content for dedup
    tags    TEXT,                    -- space-separated tags for FTS5 (knowledge sources)
    category TEXT,                   -- auto_category from frontmatter (knowledge sources)
    PRIMARY KEY (source, path)
);

-- FTS5 virtual table (shared — porter stemming)
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title,
    content,
    tags,                            -- searchable tags
    tokenize='porter',
    content='docs',
    content_rowid='rowid'
);

-- Phase 2: vector table (shared)
CREATE VIRTUAL TABLE IF NOT EXISTS docs_vec USING vec0(
    embedding float[256]
);

-- File tracking for sync (knowledge files only)
CREATE TABLE IF NOT EXISTS knowledge_files (
    path    TEXT PRIMARY KEY,
    source  TEXT NOT NULL,
    hash    TEXT NOT NULL,
    mtime   REAL NOT NULL
);
```

### FTS5 Query

```sql
-- recall_memory("pytest testing", tags=["preference"])
SELECT d.source, d.path, d.title, d.tags, d.category,
       snippet(docs_fts, 1, '>', '<', '...', 40) AS snippet,
       bm25(docs_fts) AS rank
  FROM docs_fts
  JOIN docs d ON d.rowid = docs_fts.rowid
 WHERE docs_fts MATCH '"pytest" AND "testing"'
   AND d.source = 'memory'
   AND d.tags LIKE '%preference%'
 ORDER BY rank
 LIMIT 5;
```

---

## Phased Implementation

### Phase 1: FTS5 (BM25)

**Goal:** Ranked keyword search via persistent index. Memory files as first consumer, Obsidian as second.

**Dependency:** None (SQLite FTS5 is built-in)

**New module:** `co_cli/knowledge_index.py`

```
class KnowledgeIndex:
    """FTS5 search index for all text sources."""

    def __init__(self, db_path: Path)
    def index(self, source: str, path: str, title: str, content: str, mtime: float,
              tags: str | None = None, category: str | None = None) -> None
        """Upsert a document into the index."""
    def search(self, query: str, source: str | None, tags: list[str] | None, limit: int) -> list[SearchResult]
        """BM25-ranked search with optional source and tag filtering."""
    def sync_dir(self, source: str, directory: Path, glob: str = "*.md") -> int
        """Sync index from files on disk. Hash-based change detection. Returns count indexed."""
    def needs_reindex(self, source: str, path: str, mtime: float) -> bool
        """Check if a document needs re-indexing based on mtime."""
    def remove_stale(self, source: str, current_paths: set[str]) -> int
        """Remove indexed docs whose files no longer exist."""
```

**Integration:**

- Add `knowledge_index: KnowledgeIndex` to `CoDeps`, initialized in `main.py`
- `recall_memory()` delegates to `knowledge_index.search(query, source="memory")`
- `save_memory()` calls `knowledge_index.index()` after writing the markdown file
- `search_notes()` delegates to `knowledge_index.search(query, source="obsidian")` with folder/tag post-filtering
- On agent startup: `knowledge_index.sync_dir("memory", knowledge_dir / "memories")` to catch external edits
- Obsidian connector: `sync_dir("obsidian", vault_path)` lazily on first `search_notes()` call, mtime-based incremental
- FTS5 query built from raw input using OpenClaw's pattern: tokenize → quote → AND-join

**Tag search:** Tags stored as space-separated string in `tags` column. FTS5 naturally matches tag tokens. For exact tag filtering, post-filter with SQL `LIKE` or `INSTR`.

**Acceptance Criteria:**

- [ ] `KnowledgeIndex` class with FTS5 schema
- [ ] `knowledge_index` field on `CoDeps`
- [ ] Hash-based sync from markdown files (startup + after save)
- [ ] `recall_memory()` returns BM25-ranked results with snippets
- [ ] `search_notes()` returns BM25-ranked results with FTS5 snippets
- [ ] Tag and category filtering works
- [ ] Source filtering: `search(query, source="memory")` vs `search(query)` (all sources)
- [ ] Markdown files remain source of truth — deleting `search.db` and restarting rebuilds the index
- [ ] Existing `save_memory` dedup (rapidfuzz) continues to work — FTS5 is for retrieval, not dedup

---

### Phase 2: Hybrid Search (FTS5 + Vector)

**Goal:** Add semantic similarity so "notes about productivity" finds memories about "getting things done" or "task management".

**Dependencies:** `sqlite-vec`, embedding provider (Ollama EmbeddingGemma or API)

**Embedding strategy (adapted from OpenClaw):**

| Provider | Model | Use case |
|----------|-------|----------|
| Local (Ollama) | EmbeddingGemma-300M @ 256 dims | Default — private, fast, free |
| API fallback | Gemini `gemini-embedding-001` | When Ollama unavailable |

**Embedding cache:** Like OpenClaw, cache embeddings keyed on `(provider, model, content_hash)` to avoid re-embedding unchanged content.

**KnowledgeIndex additions:**

```
def embed(self, text: str) -> list[float]
    """Generate embedding via configured provider. Cached by content hash."""

def search(self, query, source, tags, limit) -> list[KnowledgeResult]:
    fts_results = self._fts_search(query, source, tags, limit=limit*4)
    vec_results = self._vec_search(self.embed(query), source, tags, limit=limit*4)
    return self._hybrid_merge(fts_results, vec_results)[:limit]

def _hybrid_merge(self, fts, vec, vector_weight=0.7, text_weight=0.3):
    """Weighted score merge (OpenClaw pattern). Union by doc ID, combine scores."""
```

**Score normalization (from OpenClaw):**

- BM25 rank → `1 / (1 + rank)` → [0, 1]
- Cosine distance → `1 - distance` → [0, 1]
- Combined: `0.7 * vector_score + 0.3 * text_score`

**Graceful degradation:** If Ollama/embedding unavailable, fall back to FTS5-only (Phase 1 behavior). Log a warning, don't fail.

**Acceptance Criteria:**

- [ ] `sqlite-vec` extension loaded at runtime
- [ ] Embeddings generated at index time, stored in `docs_vec`
- [ ] Embedding cache table avoids redundant API/compute calls
- [ ] Weighted hybrid merge (configurable weights via `CoDeps`)
- [ ] Fallback to FTS5-only when embedding provider unavailable
- [ ] Semantic queries ("notes about efficiency") find related memories

---

### Phase 3: Cross-Encoder Reranking

**When:** Only if Phase 2 quality is insufficient for multi-source queries.

Use a small cross-encoder GGUF (~640MB), not a full LLM call. QMD uses a dedicated reranker; Sonar uses BGE Reranker v2-m3. Both are 10-100x cheaper than an LLM call and purpose-built for relevance scoring.

**Acceptance Criteria:**

- [ ] `llama-cpp-python` dependency (or Ollama if reranker model available)
- [ ] Reranker GGUF downloaded and cached on first use
- [ ] Benchmark: reranked > hybrid-only for ambiguous cross-source queries

---

## Migration Path

| Phase | Search quality | Speed | New deps |
|-------|---------------|-------|----------|
| Current | Grep / filesystem walk (no ranking) | Fast | None |
| Phase 1 (FTS5) | BM25 ranking, persistent index | Fast | None |
| Phase 2 (Hybrid) | Semantic + keyword | Medium | sqlite-vec |
| Phase 3 (Reranker) | Cross-encoder scoring | Medium | llama-cpp-python |

**Trigger for Phase 1:** Immediately beneficial — even 10 memories benefit from BM25 ranking over grep.

**Trigger for Phase 2:** When users need semantic search (synonym matching, intent-based recall).

**Trigger for Phase 3:** Multi-source ranking quality insufficient.

---

## Tool Surface

| Tool | Backed by | Notes |
|------|-----------|-------|
| `recall_memory(query, tags?)` | `search(query, source="memory")` | Existing tool, updated |
| `search_notes(query, folder?, tag?)` | `search(query, source="obsidian")` + post-filter | Existing tool, updated |
| `search_drive_files(query)` | `search(query, source="drive")` | Existing tool, updated when Drive docs are cached |
| `search_knowledge(query)` | `search(query)` | New tool — cross-source, Phase 1+ |

---

## Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `knowledge_search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `"fts5"` | `"grep"` (legacy), `"fts5"`, `"hybrid"` |
| `knowledge_embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `"ollama"` | `"ollama"`, `"gemini"`, `"none"` |
| `knowledge_embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `"embeddinggemma"` | Ollama model name or Gemini model ID |
| `knowledge_hybrid_vector_weight` | — | `0.7` | Vector score weight in hybrid merge |
| `knowledge_hybrid_text_weight` | — | `0.3` | Text score weight in hybrid merge |

## Files

| File | Purpose |
|------|---------|
| `co_cli/knowledge_index.py` | KnowledgeIndex class (FTS5 + vector) |
| `co_cli/tools/memory.py` | Updated to delegate search to KnowledgeIndex |
| `co_cli/tools/obsidian.py` | Updated to delegate search to KnowledgeIndex |
| Future: `co_cli/tools/articles.py` | Article tools using same KnowledgeIndex |
| `~/.local/share/co-cli/search.db` | SQLite database for all sources |

## 2026 Landscape

The FTS5 → Vector → Reranker stack is the established pattern:

| Project | Stack | Notes |
|---------|-------|-------|
| [QMD](https://github.com/tobi/qmd) | FTS5 + sqlite-vec + GGUF reranker | MCP server, position-aware RRF, EmbeddingGemma-300M |
| [Sonar](https://forum.obsidian.md/t/ann-sonar-offline-semantic-search-and-agentic-ai-chat-for-obsidian-powered-by-llama-cpp/110765) | BM25 + BGE-M3 + cross-encoder | llama.cpp, fully local, 32GB+ RAM |
| [llama-stack](https://github.com/llamastack/llama-stack/issues/1158) | FTS5 + sqlite-vec | Adopting same hybrid API |

## References

- [OpenClaw memory/](~/workspace_genai/openclaw/src/memory/) — hybrid search, embedding cache, FTS5 query building, score normalization
- [SQLite FTS5](https://www.sqlite.org/fts5.html) — built-in full-text search
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — vector similarity extension
- [EmbeddingGemma-300M](https://ai.google.dev/gemma/docs/embeddinggemma/model_card) — sub-200MB embedding model
- [QMD](https://github.com/tobi/qmd) — FTS5 + sqlite-vec + reranker reference implementation
- [sqlite-vec Hybrid Search](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html)
- [SQLite RAG](https://blog.sqlite.ai/building-a-rag-on-sqlite)
- [EmbeddingGemma + SQLite tutorial](https://exploringartificialintelligence.substack.com/p/create-your-own-search-system-with)
- [llama-stack Hybrid Search](https://github.com/llamastack/llama-stack/issues/1158)
