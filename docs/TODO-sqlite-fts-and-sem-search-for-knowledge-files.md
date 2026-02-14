# TODO: SQLite FTS + Semantic Search for Knowledge Files

**Status:** Backlog
**Scope:** Memory files (`.co-cli/knowledge/memories/*.md`) and future article files (`.co-cli/knowledge/articles/*.md`)
**Reference:** [OpenClaw memory system](~/workspace_genai/openclaw/src/memory/), [TODO-cross-tool-rag.md](TODO-cross-tool-rag.md), [TODO-knowledge-articles.md](TODO-knowledge-articles.md)

## Problem

`recall_memory()` uses grep-based substring search with recency-only sorting. No relevance ranking, no semantic understanding. "Find notes about productivity" fails unless the exact word "productivity" appears in the content. This works for <200 memories but provides poor retrieval quality at any scale.

Future articles will have the same problem — grep can't rank, can't handle synonyms, can't match intent.

## Relationship to Cross-Tool RAG

`TODO-cross-tool-rag.md` defines `SearchDB` as a shared service for **external sources** (Obsidian, Drive). This TODO covers the **internal knowledge tier** — memories and articles that co-cli owns. The two share the same phased FTS5 → hybrid → reranker trajectory but differ in:

| Concern | Knowledge files (this TODO) | Cross-tool RAG |
|---------|---------------------------|----------------|
| Source of truth | Markdown files co-cli writes | External services |
| Index trigger | On `save_memory()` / `save_article()` + startup sync | On first tool search + mtime check |
| Chunking | Whole-file (memories are small) | Overlap-based (notes/docs are large) |
| Frontmatter | Indexed as structured fields (tags, source, category) | Minimal (title, path, mtime) |
| Dedup | rapidfuzz similarity on save | Not needed (external owns dedup) |

**Decision:** Knowledge files get their own index tables inside the same `search.db` database that `SearchDB` uses. Memories and articles are registered as sources (`source='memory'`, `source='article'`) in the shared schema — so `SearchDB.search(query)` can optionally span all sources including knowledge files.

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
recall_memory(query)  /  recall_article(query)  /  search_knowledge(query)
       │                         │                         │
       ▼                         ▼                         ▼
  KnowledgeIndex.search(query, source="memory"|"article"|None, tags?, limit)
       │
       ├── Phase 1: FTS5 MATCH + bm25() → ranked results
       │
       ├── Phase 2: + vec0 cosine similarity → hybrid merge
       │
       └── (Phase 3: + cross-encoder rerank — shared with SearchDB)
       │
  ┌────┴────┐
  │ search.db │  ~/.local/share/co-cli/search.db (shared with SearchDB)
  └─────────┘
```

**`KnowledgeIndex`** is a focused class for the knowledge tier. It writes to the same `search.db` as `SearchDB` but owns knowledge-specific logic (frontmatter indexing, whole-file indexing, dedup coordination). `SearchDB.search(query)` can read knowledge rows via the shared `source` column.

---

## Schema

Reuses the `docs` + `docs_fts` schema from `TODO-cross-tool-rag.md` with additional columns for frontmatter metadata:

```sql
-- Extends the docs table from SearchDB (same search.db file)
-- source='memory' or source='article' rows

-- The docs table (shared with SearchDB)
CREATE TABLE IF NOT EXISTS docs (
    source  TEXT NOT NULL,           -- 'memory', 'article', 'obsidian', 'drive', ...
    path    TEXT NOT NULL,           -- relative path within .co-cli/knowledge/
    title   TEXT,                    -- memory slug or article title
    content TEXT,                    -- full markdown body (no frontmatter)
    mtime   REAL,                    -- file mtime for change detection
    hash    TEXT,                    -- SHA256 of file content for dedup
    -- knowledge-specific metadata (NULL for non-knowledge sources)
    tags    TEXT,                    -- space-separated tags for FTS5
    category TEXT,                   -- auto_category from frontmatter
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

### Phase 1: FTS5 for Knowledge Files

**Goal:** Replace grep search with BM25-ranked full-text search. Markdown files remain source of truth.

**Dependency:** None (SQLite FTS5 is built-in)

**New module:** `co_cli/knowledge_index.py`

```
class KnowledgeIndex:
    """FTS5 index for memory and article markdown files."""

    def __init__(self, db_path: Path, knowledge_dir: Path)
    def sync(self, source: str) -> int
        """Sync index from markdown files. Hash-based change detection. Returns count indexed."""
    def index_file(self, source: str, path: Path) -> None
        """Parse frontmatter + body, upsert into docs + docs_fts."""
    def search(self, query: str, source: str | None, tags: list[str] | None, limit: int) -> list[KnowledgeResult]
        """BM25-ranked search with optional source and tag filtering."""
    def remove_stale(self, source: str, current_paths: set[str]) -> int
        """Remove indexed docs whose files no longer exist."""
```

**Integration:**

- Add `knowledge_index: KnowledgeIndex` to `CoDeps`, initialized in `main.py`
- `recall_memory()` delegates to `knowledge_index.search(query, source="memory")`
- `save_memory()` calls `knowledge_index.index_file()` after writing the markdown file
- On agent startup: `knowledge_index.sync("memory")` to catch external edits
- FTS5 query built from raw input using OpenClaw's pattern: tokenize → quote → AND-join

**Tag search:** Tags stored as space-separated string in `tags` column. FTS5 naturally matches tag tokens. For exact tag filtering, post-filter with SQL `LIKE` or `INSTR`.

**Acceptance Criteria:**

- [ ] `KnowledgeIndex` class with FTS5 schema
- [ ] `knowledge_index` field on `CoDeps`
- [ ] Hash-based sync from markdown files (startup + after save)
- [ ] `recall_memory()` returns BM25-ranked results with snippets
- [ ] Tag and category filtering works
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

Shared with `TODO-cross-tool-rag.md` Phase 3. Only if Phase 2 quality is insufficient. Not knowledge-specific — applies to all `SearchDB` results.

---

## Migration Path

| Phase | recall_memory | save_memory | New deps | Quality |
|-------|--------------|-------------|----------|---------|
| Current | grep + recency sort | write file | None | Substring match only |
| Phase 1 | FTS5 BM25 ranking | write file + index | None | Keyword ranked |
| Phase 2 | Hybrid FTS5 + vector | write file + index + embed | sqlite-vec | Semantic + keyword |

**Trigger for Phase 1:** Immediately beneficial — even 10 memories benefit from BM25 ranking over grep.

**Trigger for Phase 2:** When users need semantic search (synonym matching, intent-based recall).

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
| Future: `co_cli/tools/articles.py` | Article tools using same KnowledgeIndex |
| `~/.local/share/co-cli/search.db` | Shared SQLite database (with SearchDB) |

## References

- [OpenClaw memory/](~/workspace_genai/openclaw/src/memory/) — hybrid search, embedding cache, FTS5 query building, score normalization
- [SQLite FTS5](https://www.sqlite.org/fts5.html) — built-in full-text search
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — vector similarity extension
- [EmbeddingGemma-300M](https://ai.google.dev/gemma/docs/embeddinggemma/model_card) — sub-200MB embedding model
- [QMD](https://github.com/tobi/qmd) — FTS5 + sqlite-vec + reranker reference implementation
