# TODO: SQLite FTS + Semantic Search (Unified Knowledge System)

**Status:** Backlog — Phase 1 ready to implement
**Scope:** All text sources co-cli touches — knowledge files (memories, articles), Obsidian notes, Google Drive docs
**Reference:** [OpenClaw memory system](~/workspace_genai/openclaw/src/memory/)

## Problem

Every search in co-cli is naive. `recall_memory()` uses grep with recency-only sorting. `search_notes()` walks the filesystem with regex. `search_drive_files()` relies on the API's `fullText` query. None of them rank results by relevance, and there's no way to search across sources.

Three identified improvements:

1. **Memory and article are the same thing structurally** — both are local markdown files with YAML frontmatter. Separate directories and duplicate code for the same format is unnecessary friction.
2. **No explicit short/long-term distinction needed** — gravity (updated timestamp) + decay + the existing `personality-context` tag already cover the full spectrum from ephemeral to evergreen.
3. **Replace grep with SQLite FTS5** — O(n) grep has no ranking. BM25 is zero additional dependencies (SQLite is stdlib) and delivers relevance ordering that every peer system (OpenClaw, QMD, llama-stack) converges on.

**Outcome**: A single `.co-cli/knowledge/` flat directory. Two tools (`save_memory`, `save_article`) write to it with a `kind` field distinguishing origin. A `KnowledgeIndex` SQLite FTS5 class replaces grep in `recall_memory`. Existing memory files are migrated transparently at startup.

---

## Conceptual Model

```
All knowledge items = markdown files with YAML frontmatter
  kind: memory   → conversation-derived (preference, correction, decision, context, pattern)
  kind: article  → externally-fetched (web docs, reference material, research)

Directory: .co-cli/knowledge/*.md          (unified flat dir)
Index:     ~/.local/share/co-cli/search.db (derived, rebuildable)
```

**No short/long-term tiers.** Instead:
- **Gravity**: `updated` timestamp refresh on recall → frequently recalled items rise naturally
- **Decay**: oldest unprotected items removed when `memory_max_count` exceeded
- **`personality-context` tag**: marks items structurally injected every turn (always-relevant tier)

---

## Extended Frontmatter Schema

```yaml
# Required (unchanged)
id: int
created: ISO8601

# New optional fields
kind: memory | article        # NEW — defaults to "memory" on parse if absent
origin_url: str | null        # NEW — source URL for articles, null for memories

# source field gains new value
source: detected | user-told | auto_decay | web-fetch   # web-fetch for articles

# Existing optional fields (unchanged)
updated: ISO8601 | null
tags: list[str]
auto_category: str | null
decay_protected: bool
related: list[str]
```

Backward compatibility: files without `kind` default to `"memory"` in `_load_memories()`.

---

## Storage Layout

```
.co-cli/knowledge/               ← unified flat dir (was memories/ subdir)
  001-user-prefers-pytest.md       kind: memory
  002-python-asyncio-guide.md      kind: article, origin_url: https://...
  003-kyle-mccloskey-collab.md     kind: memory

~/.local/share/co-cli/
  search.db                        FTS5 index (DATA_DIR, existing path)
  co-cli.db                        Telemetry (existing, unchanged)
```

---

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

---

## Schema

```sql
CREATE TABLE IF NOT EXISTS docs (
    source   TEXT NOT NULL,           -- 'memory', 'article', 'obsidian', 'drive', ...
    kind     TEXT,                    -- 'memory' or 'article' (knowledge files only)
    path     TEXT NOT NULL,           -- relative path within source dir
    title    TEXT,                    -- memory slug or article title
    content  TEXT,                    -- full markdown body (no frontmatter)
    mtime    REAL,                    -- file mtime for change detection
    hash     TEXT,                    -- SHA256 of file content for dedup
    tags     TEXT,                    -- space-separated tags for FTS5
    category TEXT,                    -- auto_category from frontmatter
    created  TEXT,                    -- ISO8601 from frontmatter
    updated  TEXT,                    -- ISO8601 from frontmatter
    UNIQUE(source, path)
);

-- FTS5 virtual table (porter stemming)
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title,
    content,
    tags,                            -- searchable tags
    tokenize='porter unicode61',
    content='docs',
    content_rowid='rowid'
);

-- Sync triggers to keep FTS in sync with docs
CREATE TRIGGER docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;
CREATE TRIGGER docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
END;
CREATE TRIGGER docs_au AFTER UPDATE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
    INSERT INTO docs_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

-- Phase 2: vector table (added when sqlite-vec available)
-- CREATE VIRTUAL TABLE IF NOT EXISTS docs_vec USING vec0(embedding float[256]);
```

### FTS5 Query

```sql
-- recall_memory("pytest testing", tags=["preference"])
SELECT d.source, d.kind, d.path, d.title, d.tags, d.category, d.created, d.updated,
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

### Phase 1: Unified Directory + FTS5 (BM25)

**Goal:** Migrate to unified `.co-cli/knowledge/` flat dir; ranked keyword search via persistent FTS5 index; `save_article` tool; memory files as first consumer.

**Dependency:** None (SQLite FTS5 is built-in)

---

#### Step 1 — `co_cli/knowledge_index.py` (new file)

- [ ] Create `STOPWORDS: frozenset[str]` — common English stopwords
- [ ] Create `SearchResult` dataclass with fields: `source`, `kind`, `path`, `title`, `snippet`, `score`, `tags`, `category`, `created`, `updated`
- [ ] Create `KnowledgeIndex` class with `__init__(db_path: Path)` — opens SQLite, creates schema + triggers
- [ ] Implement schema:
  - `docs` table with columns: `rowid`, `source`, `kind`, `path`, `title`, `content`, `mtime`, `hash`, `tags`, `category`, `created`, `updated`, `UNIQUE(source, path)`
  - `docs_fts` virtual table using `fts5(title, content, tags, tokenize='porter unicode61', content='docs', content_rowid='rowid')`
  - Triggers: `docs_ai` (after insert), `docs_ad` (after delete), `docs_au` (after update) to keep FTS in sync
- [ ] Implement `index(*, source, kind, path, title, content, mtime, hash, tags=None, category=None, created=None, updated=None)` — upsert via INSERT OR REPLACE; skip if hash unchanged
- [ ] Implement `search(query, *, source=None, kind=None, tags=None, limit=5) -> list[SearchResult]` — FTS5 MATCH with BM25, optional filters; returns `[]` on empty query or no matches
- [ ] Implement `_build_fts_query(query) -> str | None` — tokenize → filter stopwords → quote → AND-join; returns None if no tokens survive
- [ ] Implement `needs_reindex(source, path, current_hash) -> bool`
- [ ] Implement `sync_dir(source, directory, glob="*.md") -> int` — parse frontmatter, hash-compare, call `index()` for changed files, call `remove_stale()`; returns count indexed
- [ ] Implement `remove_stale(source, current_paths: set[str]) -> int`
- [ ] Implement `rebuild(source, directory, glob="*.md") -> int` — wipe source rows + re-sync; for recovery
- [ ] Implement `close() -> None`
- [ ] BM25 normalization: `score = 1 / (1 + abs(rank))` → [0, 1] range

#### Step 2 — `co_cli/_frontmatter.py` (modify)

- [ ] Add optional `kind` field validation in `validate_memory_frontmatter()`: must be `"memory"` or `"article"` if present
- [ ] Add optional `origin_url` field validation: string or null if present
- [ ] Ensure backward compat: files without `kind` parse/validate without error

#### Step 3 — `co_cli/config.py` and `co_cli/deps.py` (modify)

- [ ] `config.py`: add `knowledge_search_backend: Literal["fts5", "grep"] = Field(default="fts5")` with env var `CO_KNOWLEDGE_SEARCH_BACKEND`
- [ ] `deps.py`: add `knowledge_index: Any | None = field(default=None, repr=False)` (`Any` avoids circular import)
- [ ] `deps.py`: add `knowledge_search_backend: str = "fts5"`

#### Step 4 — `co_cli/tools/memory.py` (modify)

- [ ] Change all `memory_dir` path references from `.co-cli/knowledge/memories` → `.co-cli/knowledge` (5 occurrences)
- [ ] Add `fm["kind"] = "memory"` to frontmatter written in `save_memory`
- [ ] After file write in `save_memory`: call `ctx.deps.knowledge_index.index(...)` if index available
- [ ] After decay deletes in `save_memory`: call `ctx.deps.knowledge_index.remove_stale(...)` if index available
- [ ] Add `kind` label in `recall_memory` display output: `**Memory 001** [memory]` or `**Article 002** [article]`
- [ ] Add FTS5 dispatch in `recall_memory` before grep fallback:
  ```python
  if ctx.deps.knowledge_index is not None and ctx.deps.knowledge_search_backend == "fts5":
      results = ctx.deps.knowledge_index.search(query, source="memory", limit=max_results * 4)
      # Apply gravity (touch), dedup, one-hop traversal
      # Convert SearchResult → MemoryEntry for existing display logic
  else:
      # existing grep path
  ```
- [ ] Add `list_memories` optional `kind: str | None = None` parameter for filtering
- [ ] Add `kind` column in `list_memories` display output
- [ ] Implement `save_article` tool:
  ```python
  async def save_article(
      ctx: RunContext[CoDeps],
      content: str,
      title: str,
      origin_url: str,
      tags: list[str] | None = None,
      related: list[str] | None = None,
  ) -> dict[str, Any]:
  ```
  - Frontmatter: `kind: article`, `origin_url: str`, `source: web-fetch`, `title: str`
  - Dedup by `origin_url` (exact match), not content similarity
  - Returns: `display`, `article_id`, `action` ("saved" or "consolidated")
  - After file write: call `ctx.deps.knowledge_index.index(...)` if index available

#### Step 5 — `co_cli/tools/personality.py` (modify)

- [ ] Update `memory_dir` path from `.co-cli/knowledge/memories` → `.co-cli/knowledge`

#### Step 6 — `co_cli/main.py` (modify)

- [ ] Add `_migrate_memories_dir(knowledge_dir: Path) -> None` helper:
  - Move `.co-cli/knowledge/memories/*.md` → `.co-cli/knowledge/`
  - Idempotent: skip files that already exist at destination
  - Remove empty `memories/` subdir after migration
- [ ] In `create_deps()`: call `_migrate_memories_dir(knowledge_dir)` before index init
- [ ] In `create_deps()`: initialize `KnowledgeIndex` when `settings.knowledge_search_backend == "fts5"`:
  ```python
  from co_cli.knowledge_index import KnowledgeIndex
  knowledge_index = KnowledgeIndex(DATA_DIR / "search.db")
  if knowledge_dir.exists():
      knowledge_index.sync_dir("memory", knowledge_dir)
  ```
- [ ] Pass `knowledge_index` and `knowledge_search_backend` into `CoDeps`

#### Step 7 — `co_cli/agent.py` (modify)

- [ ] Import `save_article` from `co_cli.tools.memory`
- [ ] Register: `agent.tool(save_article, requires_approval=True)`

#### Step 8 — Tests

##### `tests/test_knowledge_index.py` (new file)
- [ ] Test `KnowledgeIndex` creates schema on init
- [ ] Test `index()` inserts a doc and FTS syncs
- [ ] Test `search()` returns ranked results by BM25
- [ ] Test `search()` with stopword-only query returns `[]`
- [ ] Test `search()` filters by `source=`
- [ ] Test `search()` filters by `kind=`
- [ ] Test `needs_reindex()` returns False when hash unchanged
- [ ] Test `sync_dir()` indexes new files, skips unchanged files
- [ ] Test `remove_stale()` removes deleted paths from index
- [ ] Test `rebuild()` wipes and re-indexes

##### `tests/test_memory.py` (modify)
- [ ] Update all paths from `memories/` → `knowledge/`
- [ ] Add `kind: memory` assertions in save/recall tests
- [ ] Add FTS5 round-trip test: save memory → recall via FTS → verify result appears

##### `tests/test_memory_decay.py` (modify)
- [ ] Update seed paths from `memories/` → `knowledge/`

##### `tests/test_personality_tools.py` (modify)
- [ ] Update `memory_dir` paths from `memories/` → `knowledge/`

##### `tests/test_save_article.py` (new file)
- [ ] Test `save_article` writes file with correct frontmatter (`kind: article`, `origin_url`, `source: web-fetch`)
- [ ] Test `save_article` dedup: saving same `origin_url` twice → `action: consolidated`
- [ ] Test `recall_memory` returns article results (kind: article items are searchable)
- [ ] Test `list_memories` with `kind="article"` filter returns only articles
- [ ] Test `list_memories` with `kind="memory"` filter returns only memories

---

#### Phase 1 Verification

- [ ] `uv run pytest tests/test_memory.py -v` — existing memory tests stay green (path migration tested here)
- [ ] `uv run pytest tests/test_knowledge_index.py -v` — new knowledge index unit tests pass
- [ ] `uv run pytest tests/test_save_article.py -v` — new article save tests pass
- [ ] `uv run pytest tests/test_personality_tools.py -v` — personality path update smoke test passes
- [ ] `uv run pytest tests/test_memory_decay.py -v` — decay tests pass with updated paths
- [ ] `uv run co status` — smoke test: agent starts, memory dir migrated, index created
- [ ] `uv run python evals/eval_memory_proactive_recall.py` — integration eval passes

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

```python
def embed(self, text: str) -> list[float]:
    """Generate embedding via configured provider. Cached by content hash."""

def search(self, query, source, tags, limit) -> list[SearchResult]:
    fts_results = self._fts_search(query, source, tags, limit=limit * 4)
    vec_results = self._vec_search(self.embed(query), source, tags, limit=limit * 4)
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
| `save_article(content, title, origin_url, tags?)` | Writes to `.co-cli/knowledge/`, FTS indexed | New tool — Phase 1 |
| `list_memories(kind?)` | Filesystem scan | Existing tool, `kind` filter added |
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

---

## Files

| File | Action | Key Changes |
|------|--------|-------------|
| `co_cli/knowledge_index.py` | **Create** | KnowledgeIndex class, SearchResult, STOPWORDS, SQLite FTS5 schema + triggers |
| `co_cli/tools/memory.py` | **Modify** | unified dir path (5 occurrences), kind field, FTS dispatch in recall, save_article, post-save index call |
| `co_cli/_frontmatter.py` | **Modify** | add kind/origin_url to validate_memory_frontmatter |
| `co_cli/config.py` | **Modify** | knowledge_search_backend setting + env var |
| `co_cli/deps.py` | **Modify** | knowledge_index + knowledge_search_backend fields on CoDeps |
| `co_cli/main.py` | **Modify** | _migrate_memories_dir helper, KnowledgeIndex init, sync_dir call in create_deps |
| `co_cli/agent.py` | **Modify** | import + register save_article |
| `co_cli/tools/personality.py` | **Modify** | memory_dir path update |
| `co_cli/tools/obsidian.py` | **Modify** | delegate search_notes to KnowledgeIndex (Phase 1+) |
| `tests/test_knowledge_index.py` | **Create** | FTS5 index/search/sync/rebuild tests |
| `tests/test_memory.py` | **Modify** | update paths, add kind assertions, add FTS5 round-trip test |
| `tests/test_memory_decay.py` | **Modify** | update seed paths memories/ → knowledge/ |
| `tests/test_personality_tools.py` | **Modify** | update memory_dir paths |
| `tests/test_save_article.py` | **Create** | save_article round-trip, URL dedup, recall integration |

---

## 2026 Landscape

The FTS5 → Vector → Reranker stack is the established pattern:

| Project | Stack | Notes |
|---------|-------|-------|
| [QMD](https://github.com/tobi/qmd) | FTS5 + sqlite-vec + GGUF reranker | MCP server, position-aware RRF, EmbeddingGemma-300M |
| [Sonar](https://forum.obsidian.md/t/ann-sonar-offline-semantic-search-and-agentic-ai-chat-for-obsidian-powered-by-llama-cpp/110765) | BM25 + BGE-M3 + cross-encoder | llama.cpp, fully local, 32GB+ RAM |
| [llama-stack](https://github.com/llamastack/llama-stack/issues/1158) | FTS5 + sqlite-vec | Adopting same hybrid API |

---

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
