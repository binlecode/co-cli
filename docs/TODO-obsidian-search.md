# TODO: Obsidian Search Improvements

**Status:** Backlog
**Reference:** [QMD](https://github.com/tobi/qmd), [SQLite Hybrid Search](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html)

## Current State

`search_notes()` uses early exit - returns first N matches by file system order.

```python
for note in vault.rglob("*.md"):
    if len(results) >= limit:
        break  # Early exit - no ranking
```

## 2026 Best Practice: Hybrid Search

```
┌─────────────────────────────────────────────────────────┐
│                      Query                               │
└─────────────────────────────────────────────────────────┘
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
┌───────────────────────┐   ┌───────────────────────┐
│   BM25 (FTS5)         │   │   Vector Search       │
│   Keyword matching    │   │   Semantic similarity │
└───────────────────────┘   └───────────────────────┘
            │                           │
            └─────────────┬─────────────┘
                          ▼
┌─────────────────────────────────────────────────────────┐
│              Reciprocal Rank Fusion (RRF)               │
│              Merge & blend results                      │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              LLM Re-ranking (optional)                  │
│              Final relevance ordering                   │
└─────────────────────────────────────────────────────────┘
```

---

## Phased Implementation

### Phase 1: SQLite FTS5 (BM25)

**Goal:** Ranked keyword search with persistent index.

**Dependency:** None (SQLite built-in)

**Implementation:**

```python
# Schema (in co-cli.db)
CREATE VIRTUAL TABLE notes_fts USING fts5(
    path,
    content,
    tokenize='porter'  # Stemming: "running" matches "run"
);

# Index vault (on startup or file change)
INSERT INTO notes_fts(path, content)
SELECT path, content FROM notes;

# Search with BM25
SELECT path, snippet(notes_fts, 1, '»', '«', '...', 50) as snippet
FROM notes_fts
WHERE notes_fts MATCH ?
ORDER BY bm25(notes_fts)
LIMIT ?;
```

**Changes:**
- Add `notes_fts` table to telemetry DB
- Index vault on first search (lazy)
- Re-index on file modification (mtime check)
- Replace early exit with FTS5 query

**Acceptance Criteria:**
- [ ] Create FTS5 virtual table
- [ ] Index vault contents
- [ ] Search returns BM25-ranked results
- [ ] Incremental re-indexing on file changes

---

### Phase 2: Hybrid Search (FTS5 + Vector)

**Goal:** Combine keyword + semantic matching.

**Dependency:** `sqlite-vec`

**Implementation:**

```python
# Vector table
CREATE VIRTUAL TABLE notes_vec USING vec0(
    embedding float[384]  # all-MiniLM-L6-v2 dimensions
);

# Hybrid search
def search_notes(query: str, limit: int = 10):
    # Parallel retrieval
    fts_results = db.execute("SELECT path, bm25(notes_fts) as score FROM notes_fts WHERE notes_fts MATCH ?", [query])
    vec_results = db.execute("SELECT path, distance FROM notes_vec WHERE embedding MATCH ?", [embed(query)])

    # Reciprocal Rank Fusion
    return rrf_merge(fts_results, vec_results, k=60)[:limit]
```

**RRF Formula:**
```
score(doc) = Σ 1 / (k + rank_i(doc))
```

**Acceptance Criteria:**
- [ ] Add sqlite-vec dependency
- [ ] Generate embeddings (local model or API)
- [ ] Implement RRF fusion
- [ ] Benchmark: hybrid > FTS5-only for semantic queries

---

### Phase 3: LLM Re-ranking (Optional)

**Goal:** Use LLM to re-order top results by relevance.

**When:** Only if Phase 2 quality insufficient.

**Implementation:**

```python
def search_notes(query: str, limit: int = 10):
    # Get more candidates than needed
    candidates = hybrid_search(query, limit=50)

    # LLM re-rank top candidates
    prompt = f"Rank these notes by relevance to: {query}\n{candidates[:20]}"
    ranked = llm.rank(prompt)

    return ranked[:limit]
```

**Trade-off:** Slower (LLM call per search), but highest quality.

---

## Migration Path

| Phase | Complexity | Quality | Speed |
|-------|------------|---------|-------|
| Current (early exit) | None | Poor | Fast |
| Phase 1 (FTS5) | Low | Good | Fast |
| Phase 2 (Hybrid) | Medium | Better | Medium |
| Phase 3 (LLM re-rank) | High | Best | Slow |

**Recommendation:** Implement Phase 1 when vault > 100 notes. Phase 2 when users need semantic search. Phase 3 only if needed.

---

---

## Future: Unified Knowledge Search

The search infrastructure should support multiple knowledge sources:

```
┌─────────────────────────────────────────────────────────┐
│                   Unified Search                         │
└─────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│   Obsidian    │ │ Google Drive  │ │    Future     │
│   (local)     │ │   (API)       │ │   sources     │
└───────────────┘ └───────────────┘ └───────────────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────┐
│              SQLite FTS5 + sqlite-vec                   │
│              (unified index)                            │
└─────────────────────────────────────────────────────────┘
```

**Design considerations:**
- Index Google Drive docs locally (cache + sync)
- Common schema: `source`, `path`, `content`, `embedding`
- Single `search_knowledge(query)` tool across all sources
- Source-specific tools remain for targeted queries

---

## References

- [QMD - Local Search Engine](https://github.com/tobi/qmd)
- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [sqlite-vec Hybrid Search](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html)
- [SQLite MCP Server](https://github.com/neverinfamous/sqlite-mcp-server)
