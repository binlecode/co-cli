# TODO: Knowledge System — Flat Storage, Articles, and FTS Search

**Scope:** All text sources co-cli touches — knowledge files (memories, articles), Obsidian notes, Google Drive docs
**Reference:** [OpenClaw memory system](~/workspace_genai/openclaw/src/memory/)

---

## Problem

Every search in co-cli is naive. `recall_memory()` uses grep with recency-only sorting. `search_notes()` walks the filesystem with regex. `search_drive_files()` relies on the API's `fullText` query. None of them rank results by relevance, and there's no way to search across sources.

Three improvements, delivered in sequence:

1. **Flat storage + articles as first-class kind** — memories and articles coexist in `.co-cli/knowledge/` as siblings, distinguished by `kind` frontmatter. Existing `memories/` subdir migrated. Ships immediately, before FTS.
2. **Replace grep with SQLite FTS5** — O(n) grep has no ranking. BM25 is zero additional dependencies (SQLite is stdlib) and delivers relevance ordering that every peer system (OpenClaw, QMD, llama-stack) converges on.
3. **Hybrid semantic search** — FTS5 + sqlite-vec embeddings for synonym/intent matching.

---

## Design Decision: Flat knowledge dir

All knowledge items — memories, articles, and any future `kind` — are flat `.md` files in `.co-cli/knowledge/`. The `kind` frontmatter field is the sole type signal. No `memories/` or `articles/` subdirs.

```
.co-cli/knowledge/
  001-user-prefers-pytest.md       kind: memory
  002-python-asyncio-guide.md      kind: article
  003-kyle-mccloskey-collab.md     kind: memory
  assets/
    python-asyncio-guide/
      diagram.png
```

**Why flat, not subfolders:**

- **FTS as dominant fetch path:** once the FTS index is in place, retrieval is driven by `kind` and `source` DB columns — not filesystem paths. Grep also works equally well on a flat dir.
- **Blur line is real:** the boundary between `kind: memory` and `kind: article` is intentionally soft. A saved fact can grow into reference material. Future kinds (e.g. `kind: session`, `kind: note`) are one-field additions with no migration.
- **Subfolders deferred to IO optimization:** if OS listing degrades at 10k+ files, bucketing by prefix can be introduced as a pure IO optimization — not a semantic redesign.

`assets/` is the one allowed subdir — binary files referenced by articles, kept separate to keep `glob("*.md")` clean.

---

## Conceptual Model

```
All knowledge items = markdown files with YAML frontmatter
  kind: memory   → conversation-derived (preference, correction, decision, context, pattern)
  kind: article  → externally-fetched (web docs, reference material, research)

Directory: .co-cli/knowledge/*.md           (unified flat dir)
Assets:    .co-cli/knowledge/assets/{slug}/ (multimodal, binary)
Index:     ~/.local/share/co-cli/search.db  (derived, rebuildable)
```

**No short/long-term tiers.** Instead:
- **Gravity**: `updated` timestamp refresh on recall → frequently recalled items rise naturally
- **Decay**: oldest unprotected memories removed when `memory_max_count` exceeded (articles are decay-protected by default)
- **`personality-context` tag**: marks items structurally injected every turn

---

## Extended Frontmatter Schema

```yaml
# Required (unchanged)
id: int
created: ISO8601

# New optional fields
kind: memory | article        # defaults to "memory" on parse if absent
origin_url: str | null        # source URL for articles, null for memories

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
.co-cli/knowledge/
  001-user-prefers-pytest.md       kind: memory
  002-python-asyncio-guide.md      kind: article, origin_url: https://...
  003-kyle-mccloskey-collab.md     kind: memory
  assets/
    python-asyncio-guide/
      diagram.png

~/.local/share/co-cli/
  search.db                        FTS5 index (DATA_DIR, existing path)
  co-cli.db                        Telemetry (existing, unchanged)
```

---

## KnowledgeIndex Design Principle

`KnowledgeIndex` is a single SQLite-backed search index (`search.db`) that any source can write to. The `source` column (`'memory'`, `'article'`, `'obsidian'`, `'drive'`) distinguishes origin. Tools index text opportunistically — you can only index what you have. External sources get indexed when tools read them; there is no background crawler.

| Source | Index trigger | Chunking | Notes |
|--------|--------------|----------|-------|
| Memory | `save_memory()` + startup sync | Whole-file (small) | Frontmatter indexed (tags, category) |
| Article | `save_article()` + startup sync | Whole-file | Frontmatter indexed; decay-protected |
| Obsidian | On `search_notes()` first call, mtime-based incremental | Whole-file | Local markdown |
| Drive | On `search_drive_files()` when doc text is fetched | Whole-file | Cached locally |

---

## OpenClaw Reference

OpenClaw's memory system (`openclaw/src/memory/`) is a production-grade hybrid search pipeline. Key patterns worth adopting:

### What they do well

1. **Hybrid merge with tunable weights** — FTS5 (BM25) + sqlite-vec (cosine), merged via weighted score combination (default 70% vector / 30% keyword).
2. **Normalized scoring** — BM25 rank → `1 / (1 + rank)` → [0,1]; cosine distance → `1 - distance` → [0,1].
3. **Embedding cache** — Dedup table keyed on `(provider, model, hash)` avoids re-embedding identical content.
4. **FTS5 query building** — Tokenize raw query → AND-join quoted terms. Predictable, avoids FTS5 syntax errors from user input.
5. **Graceful degradation** — If sqlite-vec unavailable, fall back to FTS5-only. If embedding provider fails, same fallback.
6. **Source filtering** — SQL WHERE on `source` column scopes queries per origin.

### What we do differently

1. **No chunking** — Our items are whole-file (memories: ~50-200 tokens; articles: may grow). Index whole files; defer chunking until articles are large enough to warrant it.
2. **Frontmatter as first-class fields** — `kind`, `tags`, `category`, `decay_protected` are indexed columns, not opaque payload.
3. **Markdown files as source of truth** — SQLite is derived and rebuildable. On startup, sync from files (hash-based change detection). Deleting `search.db` and restarting rebuilds cleanly.

---

## Architecture

```
recall_memory(query)  /  recall_article(query)  /  search_notes(query)  /  search_knowledge(query)
       │                         │                         │                         │
       ▼                         ▼                         ▼                         ▼
  KnowledgeIndex.search(query, source=..., kind=..., tags=..., limit=...)
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
    tags,
    tokenize='porter unicode61',
    content='docs',
    content_rowid='rowid'
);

-- Sync triggers
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

### FTS5 Query example

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

## Implementation

### Prerequisite A — Immediate: Migrate memories/ → knowledge/ (ship first)

**Goal:** Flatten the `memories/` subdir into the parent `knowledge/` dir. Grep continues to work on the flat dir unchanged. This is fully independent of FTS and should ship immediately.

#### `co_cli/_frontmatter.py` (modify)
- [ ] Add optional `kind` field validation in `validate_memory_frontmatter()`: must be `"memory"` or `"article"` if present
- [ ] Add optional `origin_url` field validation: string or null if present
- [ ] Ensure backward compat: files without `kind` parse/validate without error

#### `co_cli/tools/memory.py` (modify)
- [ ] Change all 5 path references from `.co-cli/knowledge/memories` → `.co-cli/knowledge`:
  - `_save_memory_impl()` line 544
  - `save_memory()` line 661
  - `recall_memory()` line 716
  - `list_memories()` line 848
  - `_decay_summarize()` `file_path` write
- [ ] Add `fm["kind"] = "memory"` to frontmatter written in `_save_memory_impl`

#### `co_cli/tools/personality.py` (modify)
- [ ] Update `memory_dir` path from `.co-cli/knowledge/memories` → `.co-cli/knowledge`

#### `co_cli/main.py` (modify)
- [ ] Add `_migrate_memories_dir(knowledge_dir: Path) -> None`:
  - Move `.co-cli/knowledge/memories/*.md` → `.co-cli/knowledge/`
  - Idempotent: skip files already at destination
  - Remove empty `memories/` subdir after migration
- [ ] Call `_migrate_memories_dir(knowledge_dir)` at startup in `create_deps()` before any tool access

#### Tests
- [ ] `tests/test_memory.py` — update all `memories/` path references → `knowledge/`; add `kind: memory` assertion in save tests
- [ ] `tests/test_memory_decay.py` — update seed paths `memories/` → `knowledge/`
- [ ] `tests/test_personality_tools.py` — update `memory_dir` paths

#### Verification
- [ ] `uv run pytest tests/test_memory.py -v`
- [ ] `uv run pytest tests/test_memory_decay.py -v`
- [ ] `uv run pytest tests/test_personality_tools.py -v`
- [ ] `uv run co status` — agent starts, files migrated to flat `knowledge/`
- [ ] `uv run python evals/eval_memory_proactive_recall.py`

---

### Prerequisite B — Articles: Storage + Tools

**Goal:** Add `kind: article` knowledge items and the tools to write and read them. Prerequisite A must ship first. Articles sit in the same flat `knowledge/` dir; FTS integration hooks are no-ops until Phase 1 ships.

#### `co_cli/deps.py` (modify — forward-declare FTS integration point)
- [ ] Add `knowledge_index: Any | None = field(default=None, repr=False)` — allows Prereq B tools
      to safely check `if ctx.deps.knowledge_index is not None` without Phase 1 being complete

#### `co_cli/tools/memory.py` (modify)

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
  - Frontmatter: `kind: article`, `origin_url: str`, `source: web-fetch`, `title: str`, `decay_protected: true`
  - Dedup by `origin_url` exact match (not content similarity)
  - Returns: `display`, `article_id`, `action` ("saved" or "consolidated")
  - After file write: call `ctx.deps.knowledge_index.index(...)` if index available (FTS integration point — no-op until Phase 1)
- [ ] Add `list_memories` optional `kind: str | None = None` parameter for filtering by kind
- [ ] Add `kind` column in `list_memories` display output

#### `co_cli/tools/articles.py` (new file — or extend memory.py)
- [ ] Implement `recall_article(query)` — returns summary index only (title, `origin_url`, tags, first paragraph); never full body (progressive loading)
- [ ] Implement `read_article_detail(slug)` — loads full markdown body on demand

#### `co_cli/agent.py` (modify)
- [ ] Import and register `save_article` with `requires_approval=True`
- [ ] Import and register `recall_article`, `read_article_detail`

#### Tests — `tests/test_save_article.py` (new file)
- [ ] Test `save_article` writes file with correct frontmatter (`kind: article`, `origin_url`, `source: web-fetch`, `decay_protected: true`)
- [ ] Test `save_article` dedup: saving same `origin_url` twice → `action: consolidated`
- [ ] Test `recall_article` returns summary only, not full body
- [ ] Test `read_article_detail` returns full body
- [ ] Test `list_memories(kind="article")` returns only articles
- [ ] Test `list_memories(kind="memory")` returns only memories

#### Future: Learn Mode (Prompt Overlay)
Knowledge curation via `"learn"` mode overlay — not a separate agent. Main chat agent with overlay uses `web_search`, `web_fetch`, `save_memory`, `save_article`. Agent classifies input, researches, proposes structured saves. User approves via standard approval flow. Wired through `get_mode_overlay("learn")` in prompt assembly (see DESIGN-16-prompt-design.md).

#### Future: Multimodal Assets
- Asset directory: `.co-cli/knowledge/assets/{slug}/`
- Frontmatter reference: `assets: [diagram.png, example.py]`
- `.gitignore` for large binary assets

---

### Phase 1 — FTS5 (BM25)

**Goal:** Ranked keyword search via persistent SQLite FTS5 index. Both memories and articles indexed. Grep fallback retained.

**Dependency:** Prerequisites A and B shipped. SQLite FTS5 is built-in — no new deps.

#### Step 1 — `co_cli/knowledge_index.py` (new file)

- [ ] Create `STOPWORDS: frozenset[str]` — common English stopwords
- [ ] Create `SearchResult` dataclass: `source`, `kind`, `path`, `title`, `snippet`, `score`, `tags`, `category`, `created`, `updated`
- [ ] Create `KnowledgeIndex` class with `__init__(db_path: Path)` — opens SQLite, creates schema + triggers
- [ ] Implement schema: `docs` table, `docs_fts` virtual table, `docs_ai`/`docs_ad`/`docs_au` triggers
- [ ] Implement `index(*, source, kind, path, title, content, mtime, hash, tags=None, category=None, created=None, updated=None)` — upsert via INSERT OR REPLACE; skip if hash unchanged
- [ ] Implement `search(query, *, source=None, kind=None, tags=None, limit=5) -> list[SearchResult]` — FTS5 MATCH + BM25; returns `[]` on empty query or no matches
- [ ] Implement `_build_fts_query(query) -> str | None` — tokenize → filter stopwords → quote → AND-join; returns None if no tokens survive
- [ ] Implement `needs_reindex(source, path, current_hash) -> bool`
- [ ] Implement `sync_dir(source, directory, glob="*.md") -> int` — parse frontmatter, hash-compare, call `index()` for changed files, call `remove_stale()`; returns count indexed
- [ ] Implement `remove_stale(source, current_paths: set[str]) -> int`
- [ ] Implement `rebuild(source, directory, glob="*.md") -> int` — wipe source rows + re-sync
- [ ] Implement `close() -> None`
- [ ] BM25 normalization: `score = 1 / (1 + abs(rank))` → [0, 1] range

#### Step 2 — `co_cli/config.py` and `co_cli/deps.py` (modify)

- [ ] `config.py`: add `knowledge_search_backend: Literal["fts5", "grep"] = Field(default="fts5")` with env var `CO_KNOWLEDGE_SEARCH_BACKEND`
- [ ] `deps.py`: add `knowledge_search_backend: str = "fts5"`

#### Step 3 — `co_cli/tools/memory.py` (modify — FTS integration)

- [ ] Add FTS5 dispatch in `recall_memory` before grep fallback:
  ```python
  if ctx.deps.knowledge_index is not None and ctx.deps.knowledge_search_backend == "fts5":
      results = ctx.deps.knowledge_index.search(query, source="memory", limit=max_results * 4)
      # Apply gravity (touch), dedup, one-hop traversal
      # Convert SearchResult → MemoryEntry for existing display logic
  else:
      # existing grep path
  ```
- [ ] Add `kind` label in `recall_memory` display: `**Memory 001** [memory]` or `**Article 002** [article]`
- [ ] After file write in `save_memory`: activate `ctx.deps.knowledge_index.index(...)` call (was no-op until now)
- [ ] After decay deletes in `save_memory`: call `ctx.deps.knowledge_index.remove_stale(...)` if index available

#### Step 4 — `co_cli/main.py` (modify — KnowledgeIndex init)

- [ ] In `create_deps()`: initialize `KnowledgeIndex` when `settings.knowledge_search_backend == "fts5"`:
  ```python
  from co_cli.knowledge_index import KnowledgeIndex
  knowledge_index = KnowledgeIndex(DATA_DIR / "search.db")
  if knowledge_dir.exists():
      knowledge_index.sync_dir("memory", knowledge_dir)
  ```
- [ ] Pass `knowledge_index` and `knowledge_search_backend` into `CoDeps`

#### Step 5 — Tests

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

##### `tests/test_memory.py` (modify — FTS additions)
- [ ] Add FTS5 round-trip: save memory → recall via FTS → verify result appears

##### `tests/test_save_article.py` (modify — FTS integration)
- [ ] Test `recall_memory` with FTS backend finds `kind: article` items
- [ ] Test `recall_article` with FTS backend returns ranked results

#### Phase 1 Verification

- [ ] `uv run pytest tests/test_knowledge_index.py -v`
- [ ] `uv run pytest tests/test_memory.py -v` — FTS round-trip passes
- [ ] `uv run pytest tests/test_save_article.py -v` — FTS integration passes
- [ ] `uv run co status` — index created, both memories and articles synced
- [ ] `uv run python evals/eval_memory_proactive_recall.py`

---

### Phase 2 — Hybrid Search (FTS5 + Vector)

**Goal:** Semantic similarity — "notes about productivity" finds memories about "getting things done".

**Dependencies:** `sqlite-vec`, embedding provider (Ollama EmbeddingGemma or API)

**Embedding strategy:**

| Provider | Model | Use case |
|----------|-------|----------|
| Local (Ollama) | EmbeddingGemma-300M @ 256 dims | Default — private, fast, free |
| API fallback | Gemini `gemini-embedding-001` | When Ollama unavailable |

**Embedding cache:** keyed on `(provider, model, content_hash)` — avoids re-embedding unchanged content (OpenClaw pattern).

**KnowledgeIndex additions:**

```python
def embed(self, text: str) -> list[float]:
    """Generate embedding via configured provider. Cached by content hash."""

def search(self, query, source, tags, limit) -> list[SearchResult]:
    fts_results = self._fts_search(query, source, tags, limit=limit * 4)
    vec_results = self._vec_search(self.embed(query), source, tags, limit=limit * 4)
    return self._hybrid_merge(fts_results, vec_results)[:limit]

def _hybrid_merge(self, fts, vec, vector_weight=0.7, text_weight=0.3):
    """Weighted score merge. Union by doc ID, combine scores."""
```

**Score normalization:** BM25 rank → `1 / (1 + rank)` → [0,1]; cosine distance → `1 - distance` → [0,1]; combined: `0.7 * vec + 0.3 * fts`.

**Graceful degradation:** fall back to FTS5-only if embedding provider unavailable.

**Acceptance Criteria:**

- [ ] `sqlite-vec` extension loaded at runtime
- [ ] Embeddings generated at index time, stored in `docs_vec`
- [ ] Embedding cache avoids redundant calls
- [ ] Weighted hybrid merge (configurable weights via `CoDeps`)
- [ ] Fallback to FTS5-only when embedding provider unavailable
- [ ] Semantic queries find related-but-not-exact memories

---

### Phase 3 — Cross-Encoder Reranking

**When:** Only if Phase 2 quality is insufficient for multi-source queries.

Use a small cross-encoder GGUF (~640MB). QMD uses a dedicated reranker; Sonar uses BGE Reranker v2-m3. Both are 10-100x cheaper than an LLM call.

**Acceptance Criteria:**

- [ ] `llama-cpp-python` dependency (or Ollama reranker model)
- [ ] Reranker GGUF downloaded and cached on first use
- [ ] Benchmark: reranked > hybrid-only for ambiguous cross-source queries

---

## Evolution Path

| Step | What ships | Search quality | New deps |
|------|-----------|---------------|----------|
| Prereq A | Flat dir migration | Grep (unchanged) | None |
| Prereq B | Articles + tools | Grep on both kinds | None |
| Phase 1 | FTS5 index | BM25 ranked | None |
| Phase 2 | Hybrid search | Semantic + keyword | sqlite-vec |
| Phase 3 | Reranker | Cross-encoder | llama-cpp-python |

**Trigger for Prereq A:** Now — `memories/` subdir is a historical artifact, no reason to keep it.

**Trigger for Prereq B:** After A — articles are the next knowledge kind needed.

**Trigger for Phase 1:** After B — even 10 items benefit from BM25 ranking over grep.

**Trigger for Phase 2:** When synonym/intent matching is needed.

**Trigger for Phase 3:** Multi-source ranking quality insufficient.

---

## Tool Surface

| Tool | Backed by | Notes |
|------|-----------|-------|
| `recall_memory(query, tags?)` | `search(query, source="memory")` | Existing, updated for FTS |
| `save_article(content, title, origin_url, tags?)` | Writes flat `kind: article` file | New — Prereq B |
| `recall_article(query)` | `search(query, kind="article")` + summary-only return | New — Prereq B |
| `read_article_detail(slug)` | Direct file read | New — Prereq B, progressive loading |
| `list_memories(kind?)` | Filesystem scan | Existing, `kind` filter added in Prereq B |
| `search_notes(query, folder?, tag?)` | `search(query, source="obsidian")` + post-filter | Existing, updated for FTS |
| `search_drive_files(query)` | `search(query, source="drive")` | Existing, updated when Drive docs cached |
| `search_knowledge(query)` | `search(query)` | New — cross-source, Phase 1+ |

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
| `co_cli/_frontmatter.py` | **Modify** | add `kind` / `origin_url` validation (Prereq A) |
| `co_cli/tools/memory.py` | **Modify** | flat path migration, `kind: memory` write, `save_article`, `list_memories(kind=)`, FTS dispatch |
| `co_cli/tools/personality.py` | **Modify** | flat path (Prereq A) |
| `co_cli/main.py` | **Modify** | `_migrate_memories_dir` (Prereq A), `KnowledgeIndex` init (Phase 1) |
| `co_cli/agent.py` | **Modify** | register `save_article`, `recall_article`, `read_article_detail` (Prereq B) |
| `co_cli/tools/articles.py` | **Create** | `recall_article`, `read_article_detail` (Prereq B) |
| `co_cli/knowledge_index.py` | **Create** | `KnowledgeIndex` class, `SearchResult`, `STOPWORDS`, FTS5 schema + triggers (Phase 1) |
| `co_cli/config.py` | **Modify** | `knowledge_search_backend` setting (Phase 1) |
| `co_cli/deps.py` | **Modify** | `knowledge_index` field (Prereq B), `knowledge_search_backend` field (Phase 1) |
| `co_cli/tools/obsidian.py` | **Modify** | delegate `search_notes` to `KnowledgeIndex` (Phase 1) |
| `tests/test_memory.py` | **Modify** | update paths (Prereq A), `kind` assertions (Prereq B), FTS round-trip (Phase 1) |
| `tests/test_memory_decay.py` | **Modify** | update seed paths (Prereq A) |
| `tests/test_personality_tools.py` | **Modify** | update `memory_dir` paths (Prereq A) |
| `tests/test_save_article.py` | **Create** | article round-trip, URL dedup, `kind=` filter, FTS integration (Prereq B + Phase 1) |
| `tests/test_knowledge_index.py` | **Create** | FTS index/search/sync/rebuild (Phase 1) |

---

## 2026 Landscape

The FTS5 → Vector → Reranker stack is the established pattern:

| Project | Stack | Notes |
|---------|-------|-------|
| [QMD](https://github.com/tobi/qmd) | FTS5 + sqlite-vec + GGUF reranker | MCP server, EmbeddingGemma-300M, position-aware RRF |
| [Sonar](https://forum.obsidian.md/t/ann-sonar-offline-semantic-search-and-agentic-ai-chat-for-obsidian-powered-by-llama-cpp/110765) | BM25 + BGE-M3 + cross-encoder | llama.cpp, fully local |
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
- [llama-stack Hybrid Search](https://github.com/llamastack/llama-stack/issues/1158)
