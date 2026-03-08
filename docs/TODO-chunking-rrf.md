# TODO: Article Chunking + RRF Hybrid Merge

Two independent but related improvements to the knowledge search pipeline.
Both ship in the same delivery. Chunking fixes recall depth; RRF fixes merge quality.

---

## Background

### Why chunking

Long-form knowledge sources (saved articles, Obsidian notes, Drive docs) are indexed
as a single `docs` row with one FTS entry and one embedding. Facts buried deep in a
3 000-word page dilute their embedding signal across the whole document. Three peer
systems confirm this gap:

- `openclaw`: `chunks` table with `start_line`/`end_line`, `chunks_fts`, `chunks_vec`
- `letta`: archival passages are atomic per-passage vectors
- `mem0`: LLM fact extraction = implicit chunking

Memory entries are already atomic by design (50–300 chars). Chunking them adds schema
complexity for zero recall benefit. **Memories skip chunking entirely.**

### Why RRF over weighted score merge

The current `_hybrid_merge()` does:

```
combined = vector_weight * vec_score + text_weight * fts_score
```

This requires:
1. Both scores to be on the same scale (they are not — BM25 is unbounded, cosine is [0,1])
2. Careful calibration of `vector_weight`/`text_weight` to avoid one leg dominating
3. Zero-fill for docs that only appear in one leg, which suppresses them unfairly

Reciprocal Rank Fusion (RRF):

```
rrf_score = sum(1 / (k + rank_i))   for each ranked list i
```

- Rank-based, not score-based — no normalization needed across legs
- A doc that ranks #1 in FTS and #3 in vec gets a strong combined score regardless of
  raw score magnitude
- `k=60` is the standard constant (from the original Cormack 2009 paper); robust across
  corpora
- Proven to match or beat weighted merge in information retrieval benchmarks without
  tuning

RRF is a Python computation over ranked lists — no schema change, no DB migration,
no new dependencies. It's a 10-line swap inside `_hybrid_merge()`.

---

## Scope

| Source | Chunking | Notes |
|--------|----------|-------|
| `memory` | NO | Atomic by design; skip via guard in `index_chunks` |
| `library` | YES | Saved articles |
| `obsidian` | YES | Vault notes |
| `drive` | YES | Indexed on `read_drive_file` |

---

## TASK-1: `co_cli/_chunker.py` (NEW FILE)

Minimal paragraph-aware text splitter. No external dependencies.

### Public interface

```python
@dataclass
class Chunk:
    index: int       # 0-based position within the document
    content: str     # chunk text (may include overlap prefix from previous chunk)
    start_line: int  # 0-based line index of first line in original text
    end_line: int    # 0-based line index of last line (inclusive)

def chunk_text(
    text: str,
    chunk_size: int = 512,
    overlap: int = 64,
) -> list[Chunk]:
    ...
```

### Algorithm

Token estimation: `len(text) / 4` (chars ÷ 4 ≈ tokens). Integer arithmetic only —
no tokenizer dependency.

Split strategy (in priority order):
1. Split on blank lines (paragraph boundaries). Accumulate paragraphs until adding
   the next would exceed `chunk_size` tokens.
2. When a single paragraph exceeds `chunk_size` tokens on its own, split it at line
   boundaries within the paragraph.
3. When a single line exceeds `chunk_size`, hard-split at character position.

Overlap: the last `overlap` tokens of the previous chunk are prepended to the start
of the next chunk (sliding window). This ensures a phrase split across a chunk boundary
remains findable in at least one chunk.

Short-document rule: if `len(text) / 4 <= chunk_size`, return a single `Chunk` with
`index=0`, `start_line=0`, `end_line=len(lines)-1`. No splitting or overlap applied.

Memory guard: callers are responsible for not passing `source="memory"` content.
`_chunker.py` itself has no source awareness.

### Edge cases to handle

- Empty string → `[Chunk(index=0, content="", start_line=0, end_line=0)]`
- Text with no blank lines → paragraph = whole text → falls through to line-level split
- Windows-style `\r\n` line endings → normalise to `\n` before processing
- `overlap >= chunk_size` → clamp overlap to `chunk_size // 4` silently (defensive)

---

## TASK-2: `co_cli/knowledge_index.py` — Schema + indexing + search routing

### 2a. New tables in `_SCHEMA_SQL`

```sql
CREATE TABLE IF NOT EXISTS chunks (
    source      TEXT NOT NULL,
    doc_path    TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content     TEXT,
    start_line  INTEGER,
    end_line    INTEGER,
    hash        TEXT,
    PRIMARY KEY (source, doc_path, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    tokenize='porter unicode61',
    content='chunks',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
    INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;
```

In hybrid mode, also create `chunks_vec`:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
    embedding float[{embedding_dims}]
)
```

keyed by `chunks.rowid`. Existing `docs_vec` is left in place for backward compat
(existing dbs) but is **no longer written** for new content once chunking ships.

### 2b. Safe migration for existing `search.db`

`CREATE TABLE IF NOT EXISTS` and `CREATE VIRTUAL TABLE IF NOT EXISTS` are idempotent.
No explicit migration SQL needed for new tables.

For `chunks_vec` (hybrid mode only), add to the hybrid init block after `docs_vec`
creation — same `IF NOT EXISTS` guard.

No `ALTER TABLE` needed (chunks is a new table, not an extension of docs).

On startup with an existing db and no chunk rows: bootstrap re-sync via `sync_dir`
will populate them automatically on next search or startup call.

### 2c. `__init__` signature change

Add two new params:

```python
def __init__(
    self,
    db_path: Path,
    *,
    ...existing params...,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> None:
```

Store as `self._chunk_size` and `self._chunk_overlap`. Used by `index_chunks` and
passed through from `sync_dir`.

### 2d. New method: `index_chunks(source, doc_path, chunks)`

```python
def index_chunks(
    self,
    source: str,
    doc_path: str,
    chunks: list[Chunk],
) -> None:
```

Logic:
1. Guard: `if source == "memory": raise ValueError("memory source must not be chunked")`
2. Delete all existing `chunks` rows for `(source, doc_path)` in one DELETE statement.
   The `chunks_ad` trigger fires per-row and cleans `chunks_fts` automatically.
3. In hybrid mode: delete corresponding `chunks_vec` rows. Strategy: collect rowids
   before deleting from `chunks`, then bulk-delete from `chunks_vec`.
4. Insert all chunks in a single transaction. Per-chunk: check `hash` for skip-on-match
   (same pattern as `index()` — avoids re-embedding unchanged chunks).
5. In hybrid mode: generate one embedding per chunk via `_get_embedding(chunk.content)`.
   Insert into `chunks_vec` using the just-inserted `chunks.rowid`.
6. Commit once after all inserts.

Skip-on-hash per chunk means re-syncing a large article that changed only in one
section re-embeds only the changed chunks, not all of them.

### 2e. New method: `remove_chunks(source, path)`

```python
def remove_chunks(self, source: str, path: str) -> None:
```

- Collect rowids from `chunks WHERE source=? AND doc_path=?`
- In hybrid mode: delete from `chunks_vec` by those rowids
- Delete from `chunks` (triggers clean `chunks_fts`)
- Commit

### 2f. Modify `remove(source, path)`

Add a call to `self.remove_chunks(source, path)` before the existing `docs` DELETE.
Order matters: rowids in `chunks_vec` reference `chunks.rowid` — delete vec first,
then chunks, then docs.

### 2g. Modify `remove_stale(source, current_paths, directory)`

In the `to_delete` loop, add `self.remove_chunks(source, path)` before the `docs`
DELETE for each stale path.

### 2h. Search routing — non-memory sources use `chunks_fts`

Current `_fts_search` queries `docs_fts JOIN docs`. After this change:

- If `source == "memory"` or (source is a list and all entries are `"memory"`):
  → use existing `docs_fts JOIN docs` path **unchanged**
- Otherwise (library, obsidian, drive, None, mixed list):
  → query `chunks_fts JOIN chunks`, group by `doc_path`, take the best-scoring
    chunk per doc, then JOIN `docs` to fetch title/tags/created/updated/provenance/
    certainty for the display fields

The group-by dedup SQL pattern (FTS5 path):

```sql
SELECT
    c.source, c.doc_path,
    snippet(chunks_fts, 0, '>', '<', '...', 40) AS snippet,
    bm25(chunks_fts) AS rank
FROM chunks_fts
JOIN chunks c ON c.rowid = chunks_fts.rowid
WHERE chunks_fts MATCH ?
  AND c.source IN (?, ?, ?)          -- source filter
  [AND c.source = ?]                  -- single source variant
ORDER BY rank
```

Then in Python: group by `doc_path`, keep the row with the best (most negative) BM25
rank per doc. Then fetch `docs` metadata for those paths.

`SearchResult.snippet` is populated from the best-matching chunk's content, not the
full doc body.

### 2i. Hybrid search routing — `chunks_vec` instead of `docs_vec`

`_vec_search` currently queries `docs_vec WHERE embedding MATCH ?`. In hybrid mode,
for non-memory sources, query `chunks_vec` instead, then group by `doc_path` (same
dedup as FTS path), take the closest chunk per doc, join `docs` for metadata.

For memory source: continue using `docs_vec` path unchanged.

---

## TASK-3: `sync_dir` — emit chunks on file sync

After the existing `self.index(...)` call for each file, add:

```python
if source != "memory":
    from co_cli._chunker import chunk_text
    chunks = chunk_text(body.strip(), chunk_size=self._chunk_size, overlap=self._chunk_overlap)
    self.index_chunks(source, path_str, chunks)
```

This covers all three non-memory sync paths:
- Bootstrap library sync (`source="library"`)
- Obsidian on-demand sync (`source="obsidian"`, triggered by `search_knowledge`)
- Drive indexing (`source="drive"`, triggered by `read_drive_file`)

The memory guard in `index_chunks` provides a second line of defence if the `if`
check is ever missed.

---

## TASK-4: `save_article` — call `index_chunks` after `index()`

In `co_cli/tools/articles.py`, both the new-article path and the consolidation path
call `ctx.deps.knowledge_index.index(...)`. After each of those calls, add:

```python
if ctx.deps.knowledge_index is not None:
    try:
        from co_cli._chunker import chunk_text
        chunks = chunk_text(content, ...)
        ctx.deps.knowledge_index.index_chunks("library", str(file_path), chunks)
    except Exception as e:
        logger.warning(f"Failed to chunk article {article_id}: {e}")
```

The chunk_size/overlap values are not available directly in the tool (tools don't
access Settings). Two options:
- Option A: add `knowledge_chunk_size` / `knowledge_chunk_overlap` to `CoDeps` (cleaner)
- Option B: hardcode defaults in the call, rely on `KnowledgeIndex.__init__` defaults

**Go with Option A** (consistent with CoDeps-is-the-source-of-truth principle).

---

## TASK-5: `co_cli/config.py` — two new settings

```python
knowledge_chunk_size: int = Field(default=512)    # env: CO_KNOWLEDGE_CHUNK_SIZE
knowledge_chunk_overlap: int = Field(default=64)  # env: CO_KNOWLEDGE_CHUNK_OVERLAP
```

Add to `fill_from_env` map:

```python
"knowledge_chunk_size":   "CO_KNOWLEDGE_CHUNK_SIZE",
"knowledge_chunk_overlap": "CO_KNOWLEDGE_CHUNK_OVERLAP",
```

### `co_cli/deps.py` — two new scalar fields

```python
knowledge_chunk_size: int = 512
knowledge_chunk_overlap: int = 64
```

### `co_cli/main.py` — thread through to KnowledgeIndex and CoDeps

In `_build_index()`:

```python
return KnowledgeIndex(
    ...,
    chunk_size=settings.knowledge_chunk_size,
    chunk_overlap=settings.knowledge_chunk_overlap,
)
```

In CoDeps construction:

```python
knowledge_chunk_size=settings.knowledge_chunk_size,
knowledge_chunk_overlap=settings.knowledge_chunk_overlap,
```

---

## TASK-6: RRF hybrid merge (replaces weighted score merge)

### Location: `KnowledgeIndex._hybrid_merge()`

Current signature:

```python
def _hybrid_merge(
    self,
    fts: list[SearchResult],
    vec: list[SearchResult],
    vector_weight: float,
    text_weight: float,
) -> list[SearchResult]:
```

Replace body with RRF. Keep the same signature so callers (`_hybrid_search`) need
no change. `vector_weight` and `text_weight` become unused — keep in signature for
backward compat, ignore in body (or add a deprecation note in the docstring).

### RRF algorithm

```
k = 60   # standard constant from Cormack 2009; robust across corpora

for each result r at rank i in fts_list:
    rrf_scores[r.path] += 1 / (k + i + 1)   # 1-based rank → i+1

for each result r at rank j in vec_list:
    rrf_scores[r.path] += 1 / (k + j + 1)
```

A doc that appears only in FTS at rank 1 scores `1/61 ≈ 0.0164`.
A doc that appears in both FTS rank 1 and vec rank 1 scores `2/61 ≈ 0.0328`.
A doc in FTS rank 1 + vec rank 5 scores `1/61 + 1/65 ≈ 0.0318`.

The merge result set is the union of both lists, deduplicated by path, sorted by
descending RRF score.

For `snippet`: prefer the FTS result's snippet (BM25 snippet() is text-grounded);
fall back to None for vec-only entries.

### Why k=60

The constant `k` controls how much a very high rank (rank 1 vs rank 2) matters
relative to a mid-list rank. `k=60` is the original paper's default and is widely
reproduced in production systems (Elasticsearch, OpenSearch hybrid search). Lower
values (e.g. k=10) amplify the top-rank signal; higher values (k=100) flatten it.
For co-cli's small corpora, `k=60` is fine and requires no tuning.

### Config implication

`hybrid_vector_weight` and `hybrid_text_weight` settings become no-ops for the
hybrid merge path. Keep them in `Settings`/`CoDeps` for backward compat (users may
have them in settings.json). Add a note in the docstring that they are ignored when
RRF is active.

---

## Files to modify / create

| Action | File | What changes |
|--------|------|-------------|
| CREATE | `co_cli/_chunker.py` | New: Chunk dataclass + `chunk_text()` |
| MODIFY | `co_cli/knowledge_index.py` | Schema, `__init__`, `index_chunks`, `remove_chunks`, `remove`, `remove_stale`, `_fts_search`, `_vec_search`, `_hybrid_merge` (RRF), `sync_dir` |
| MODIFY | `co_cli/tools/articles.py` | Call `index_chunks` in `save_article` and consolidation path |
| MODIFY | `co_cli/config.py` | 2 new settings + env var entries |
| MODIFY | `co_cli/deps.py` | 2 new scalar fields |
| MODIFY | `co_cli/main.py` | Thread chunk_size/overlap into `_build_index()` and CoDeps |
| MODIFY | `docs/DESIGN-knowledge.md` | Update §2.3 (chunks schema), §2.4 (search routing), §3.1 (config table) |
| CREATE | `tests/test_chunker.py` | New test file |
| MODIFY | `tests/test_knowledge_index.py` | Chunk indexing, FTS/hybrid via chunks, remove() cascade |
| MODIFY | `tests/test_save_article.py` | Verify chunks written on `save_article` |

---

## Testing

```bash
uv run pytest tests/test_chunker.py tests/test_knowledge_index.py tests/test_save_article.py -v
```

### test_chunker.py — scenarios

1. Short text (< chunk_size) → single chunk, index=0, start_line=0
2. Multi-paragraph text → correct chunk count, each chunk within token budget
3. Overlap: last chunk's content prefix matches end of previous chunk's content
4. Line ranges: `start_line` and `end_line` correctly bound each chunk in the original
5. Single oversized paragraph (no blank lines) → line-level split fallback
6. Empty string → returns list with one empty chunk
7. `overlap >= chunk_size` → clamped without error

### test_knowledge_index.py — new scenarios

8. `chunks` and `chunks_fts` tables exist after init (FTS5 mode)
9. `index_chunks` inserts correct row count into `chunks` table
10. FTS search on non-memory source queries `chunks_fts`: phrase present only in
    second half of article is retrievable (the core regression this plan fixes)
11. `remove()` on an article also removes its chunk rows (cascade test)
12. `sync_dir` for `library` source emits chunks; `sync_dir` for `memory` source
    does NOT (check chunks table is empty for memory source)
13. `recall_memory` path unchanged: queries `docs_fts`, not `chunks_fts`
14. `index_chunks` with `source="memory"` raises `ValueError`
15. Re-sync unchanged article: chunk hash skip — no new rows, no re-embedding

### RRF-specific scenarios

16. `_hybrid_merge` with two ranked lists: doc present in both lists scores higher
    than doc in only one list (regardless of raw score values)
17. RRF is rank-based: a doc with artificially inflated raw score but low rank does
    not dominate a doc with high rank in both lists
18. Union: doc in only FTS list and doc in only vec list both appear in merged result
19. Snippet preference: FTS-side snippet is used when available; vec-only entry has
    snippet=None

### test_save_article.py — new scenario

20. End-to-end: save a long article (>2 × chunk_size) and search for a phrase that
    appears only in the second half — must be retrievable via chunks FTS

---

## Known limitations (deferred)

- `docs_vec` (per-doc embedding) is left in place for existing dbs but no longer
  written. Full cleanup requires explicit migration — deferred.
- `hybrid_vector_weight` / `hybrid_text_weight` settings become no-ops after RRF
  lands. Remove them in a future cleanup cycle.
- Chunk size/overlap tuned for English prose. CJK and code-heavy docs may benefit
  from different defaults — deferred.
- Drive docs: re-chunked only on next `read_drive_file` call (consistent with
  existing Drive lifecycle). No background re-chunk on Drive content change.
- `read_article_detail` returns full article body unchanged. Section-aware response
  using chunks (return only the most relevant chunk) is a follow-on enhancement.
- RRF `k=60` is hardcoded. Making it configurable adds no practical value at
  co-cli's scale — defer.
