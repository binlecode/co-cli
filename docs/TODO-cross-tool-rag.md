# TODO: Cross-Tool RAG (SearchDB)

**Status:** Backlog
**Reference:** [QMD](https://github.com/tobi/qmd), [sqlite-vec Hybrid Search](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html), [Sonar](https://forum.obsidian.md/t/ann-sonar-offline-semantic-search-and-agentic-ai-chat-for-obsidian-powered-by-llama-cpp/110765)

## Problem

Every knowledge source in co-cli implements its own search — Obsidian walks the filesystem, Drive calls the API, Gmail has a separate query syntax. None of them rank results by relevance, and there's no way to search across sources.

Phases 1–3 below build a naive RAG pipeline (**index → retrieve → rank**) as a shared service that any tool can use. Instead of baking search into `obsidian.py` and rebuilding it for Drive, the infrastructure lives in one place.

## 2026 Landscape

The FTS5 → Vector → Reranker stack is the established pattern:

| Project | Stack | Notes |
|---------|-------|-------|
| [QMD](https://github.com/tobi/qmd) | FTS5 + sqlite-vec + GGUF reranker | MCP server, position-aware RRF, EmbeddingGemma-300M |
| [Sonar](https://forum.obsidian.md/t/ann-sonar-offline-semantic-search-and-agentic-ai-chat-for-obsidian-powered-by-llama-cpp/110765) | BM25 + BGE-M3 + cross-encoder | llama.cpp, fully local, 32GB+ RAM |
| [llama-stack](https://github.com/llamastack/llama-stack/issues/1158) | FTS5 + sqlite-vec | Adopting same hybrid API |

Key choices validated by the ecosystem:
- **Embedding model:** [EmbeddingGemma-300M](https://ai.google.dev/gemma/docs/embeddinggemma/model_card) — sub-200MB, Matryoshka dims (768→128), runs in Ollama
- **Reranker:** Dedicated cross-encoder GGUF (~640MB), not a full LLM call
- **RRF:** Position-aware weighting (QMD: 75/25 top-3, 60/40 4–10, 40/60 11+)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                          CoDeps                              │
│                            │                                 │
│                      ┌─────┴─────┐                           │
│                      │ SearchDB  │  co_cli/search_db.py      │
│                      └─────┬─────┘                           │
│                            │                                 │
│                ┌───────────┼───────────┐                     │
│                ▼           ▼           ▼                      │
│           docs_fts    docs_vec    docs table                 │
│           (FTS5)      (vec0)     (source, path, content,     │
│                                   mtime, embedding)          │
└──────────────────────────────────────────────────────────────┘
         ~/.local/share/co-cli/search.db
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
     ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
     │  Obsidian   │ │ Google Drive│ │   Future    │
     │  connector  │ │  connector  │ │  connectors │
     │  (rglob)    │ │  (API+cache)│ │             │
     └─────────────┘ └─────────────┘ └─────────────┘
```

**`SearchDB`** is a class on `CoDeps` (like `sandbox`). It owns a dedicated SQLite database — separate from the telemetry DB — with FTS5 and vector tables behind a common schema. Connectors are thin adapters that feed documents into the index; tools delegate search to `SearchDB`.

### Source Applicability

| Source | Current search | Benefit from SearchDB |
|--------|---------------|----------------------|
| Obsidian | `rglob` + regex (no ranking) | **High** — local text, ideal for FTS5 + vector |
| Google Drive | API `fullText` query | **High** — cache docs locally, ranked offline search |
| Gmail | API `q` parameter | **Low** — structured metadata, API search sufficient |
| Calendar | API `q` parameter | **None** — structured data, filtering not retrieval |
| Slack | N/A | **Medium** — channel history indexing for context |

### Search Flow

```
Tool (search_notes / search_drive / search_knowledge)
  │
  ▼
SearchDB.search(query, source?, limit)
  │
  ├── FTS5 MATCH + BM25 score    ──┐
  │                                 ├── RRF merge ──► results
  └── vec0 cosine similarity     ──┘
                                        │
                                   (Phase 3 only)
                                        ▼
                                   cross-encoder rerank
```

---

## Phased Implementation

### Phase 1: SearchDB + FTS5 (BM25)

**Goal:** Ranked keyword search via persistent index. Shared service, Obsidian as first connector.

**Dependency:** None (SQLite built-in)

**New module:** `co_cli/search_db.py`

```python
class SearchDB:
    """Cross-tool search index backed by SQLite FTS5."""

    def __init__(self, db_path: Path):
        self.db = sqlite3.connect(db_path)
        self._ensure_schema()

    def index(self, source: str, path: str, title: str, content: str, mtime: float) -> None:
        """Upsert a document into the index."""
        ...

    def search(self, query: str, source: str | None = None, limit: int = 10) -> list[SearchResult]:
        """BM25-ranked search, optionally filtered by source."""
        ...

    def needs_reindex(self, source: str, path: str, mtime: float) -> bool:
        """Check if a document needs re-indexing based on mtime."""
        ...

    def remove_stale(self, source: str, current_paths: set[str]) -> int:
        """Remove documents no longer present in the source. Returns count removed."""
        ...
```

**Schema:**
```sql
-- ~/.local/share/co-cli/search.db

CREATE TABLE docs (
    source TEXT NOT NULL,        -- 'obsidian', 'drive', etc.
    path   TEXT NOT NULL,        -- relative path or doc ID
    title  TEXT,                 -- note title / doc name
    content TEXT,                -- full text
    mtime  REAL,                 -- source last-modified timestamp
    PRIMARY KEY (source, path)
);

CREATE VIRTUAL TABLE docs_fts USING fts5(
    title,
    content,
    tokenize='porter',           -- stemming: "running" matches "run"
    content='docs',              -- external content table
    content_rowid='rowid'
);

-- Search query
SELECT d.source, d.path, d.title,
       snippet(docs_fts, 1, '»', '«', '...', 50) AS snippet
FROM docs_fts
JOIN docs d ON d.rowid = docs_fts.rowid
WHERE docs_fts MATCH ?
  AND (? IS NULL OR d.source = ?)
ORDER BY bm25(docs_fts)
LIMIT ?;
```

**Integration:**
- Add `search_db: SearchDB` to `CoDeps`, initialized in `main.py`
- Obsidian connector: index vault lazily on first `search_notes()` call, re-index on mtime change
- `search_notes()` delegates to `search_db.search(query, source="obsidian")` with folder/tag post-filtering
- Drive connector follows as second consumer (see Connectors section)

**Acceptance Criteria:**
- [ ] `SearchDB` class with FTS5 schema (source-aware)
- [ ] `search_db` field on `CoDeps`
- [ ] Obsidian connector: lazy index, mtime-based incremental re-index, stale cleanup
- [ ] `search_notes()` returns BM25-ranked results with FTS5 snippets
- [ ] Source filtering: `search(query, source="obsidian")` vs `search(query)` (all sources)

---

### Phase 2: Hybrid Search (FTS5 + Vector)

**Goal:** Combine keyword matching with semantic similarity.

**Dependencies:** `sqlite-vec`, Ollama with `embeddinggemma` model

**Why EmbeddingGemma-300M:**
- Sub-200MB with quantization-aware training
- Matryoshka dims — configurable from 768 down to 128
- Runs in Ollama (co-cli already supports `LLM_PROVIDER=ollama`)
- Better benchmarks than all-MiniLM-L6-v2 at equivalent size

**Schema extension:**
```sql
CREATE VIRTUAL TABLE docs_vec USING vec0(
    embedding float[256]         -- Matryoshka @ 256 dims
);
```

**SearchDB additions:**
```python
def embed(self, text: str) -> list[float]:
    """Generate embedding via Ollama embeddinggemma."""
    ...

def search(self, query: str, source: str | None = None, limit: int = 10) -> list[SearchResult]:
    fts_results = self._fts_search(query, source, limit=50)
    vec_results = self._vec_search(self.embed(query), source, limit=50)
    return self._rrf_merge(fts_results, vec_results)[:limit]

def _rrf_merge(self, *result_lists: list, k: int = 60) -> list[SearchResult]:
    """Position-aware Reciprocal Rank Fusion (QMD-style).

    Weight by rank position:
      ranks 1–3:   75% retrieval + 25% reranker
      ranks 4–10:  60/40
      ranks 11+:   40/60
    """
    ...
```

**Integration:**
- Embeddings generated at index time (stored in `docs_vec`)
- Query embedding generated at search time
- Graceful degradation: if Ollama unavailable, fall back to FTS5-only

**Acceptance Criteria:**
- [ ] `sqlite-vec` dependency
- [ ] Embeddings via Ollama `embeddinggemma` with fallback to FTS5-only
- [ ] Position-aware RRF fusion
- [ ] Embeddings persisted at index time
- [ ] Benchmark: hybrid > FTS5-only for semantic queries

---

### Phase 3: Cross-Encoder Re-ranking

**Goal:** Re-order top candidates with a purpose-built reranker model.

**When:** Only if Phase 2 quality is insufficient for multi-source queries.

**Insight:** Use a small cross-encoder GGUF (~640MB), not a full LLM call. QMD uses a dedicated reranker; Sonar uses BGE Reranker v2-m3. Both are 10–100x cheaper than an LLM call and purpose-built for relevance scoring.

```python
def search(self, query: str, source: str | None = None, limit: int = 10) -> list[SearchResult]:
    candidates = self._hybrid_search(query, source, limit=50)
    scored = self._rerank(query, candidates[:20])
    return scored[:limit]
```

**Trade-off:** ~640MB model download, ~100ms per re-rank (vs ~2s for LLM).

**Acceptance Criteria:**
- [ ] `llama-cpp-python` dependency (or Ollama if reranker model available)
- [ ] Reranker GGUF downloaded and cached on first use
- [ ] Benchmark: reranked > hybrid-only for ambiguous cross-source queries

---

## Migration Path

| Phase | Adds | Quality | Speed | New deps |
|-------|------|---------|-------|----------|
| Phase 1 (FTS5) | BM25 ranking, persistent index | Good | Fast | None |
| Phase 2 (Hybrid) | Semantic similarity | Better | Medium | sqlite-vec |
| Phase 3 (Reranker) | Cross-encoder scoring | Best | Medium | llama-cpp-python |

**Triggers:**
- **Phase 1:** Vault > 100 notes, or Drive indexing desired
- **Phase 2:** Users need semantic search ("notes about productivity" vs "productivity")
- **Phase 3:** Multi-source ranking quality insufficient

---

## Connectors

Each connector feeds documents into `SearchDB`. Connectors own their sync strategy.

```python
class Connector(Protocol):
    source: str

    def reindex(self, search_db: SearchDB) -> int:
        """Full or incremental re-index. Returns document count."""
        ...
```

| Source | Connector | Sync strategy |
|--------|-----------|---------------|
| Obsidian | `rglob("*.md")` + `read_text()` | Lazy on first search, mtime-based incremental |
| Google Drive | API list + export to text | Cache locally, re-sync if stale (TTL-based) |
| Gmail | API search + message get | Index recent threads, append-only |
| Slack | `conversations.history` | Index channel history, append-only |

**Design rules:**
- Connectors registered in `CoDeps` setup, not hardcoded in `SearchDB`
- Each connector owns its sync schedule (push/pull, TTL, incremental)
- `SearchDB.search(source=None)` searches all sources; `source="obsidian"` filters
- Source-specific tools (`search_notes`, `search_drive`) remain as thin wrappers
- A future `search_knowledge(query)` tool provides unified cross-source search

---

## Tool Surface

After Phase 1, the tool layer becomes:

| Tool | Backed by | Notes |
|------|-----------|-------|
| `search_notes(query, folder?, tag?)` | `search_db.search(query, source="obsidian")` + folder/tag post-filter | Existing tool, updated |
| `search_drive(query)` | `search_db.search(query, source="drive")` | Existing tool, updated when Drive connector lands |
| `search_knowledge(query)` | `search_db.search(query)` | New tool — cross-source, Phase 1+ |

---

## References

- [QMD](https://github.com/tobi/qmd) — FTS5 + sqlite-vec + GGUF reranker, MCP server
- [Obsidian QMD Plugin](https://github.com/achekulaev/obsidian-qmd) — QMD in Obsidian
- [Sonar](https://forum.obsidian.md/t/ann-sonar-offline-semantic-search-and-agentic-ai-chat-for-obsidian-powered-by-llama-cpp/110765) — BM25 + BGE-M3 + cross-encoder, llama.cpp
- [sqlite-vec Hybrid Search](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html)
- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [EmbeddingGemma-300M](https://ai.google.dev/gemma/docs/embeddinggemma/model_card)
- [EmbeddingGemma + SQLite tutorial](https://exploringartificialintelligence.substack.com/p/create-your-own-search-system-with)
- [llama-stack Hybrid Search](https://github.com/llamastack/llama-stack/issues/1158)
- [SQLite RAG](https://blog.sqlite.ai/building-a-rag-on-sqlite)
