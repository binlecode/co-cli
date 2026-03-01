---
title: Knowledge System
nav_order: 17
---

# Knowledge System

## 1. What & How

The knowledge system is co-cli's unified store for everything the agent learns or saves across sessions. All knowledge items are flat `.md` files in `.co-cli/knowledge/`, distinguished by a `kind` frontmatter field: `memory` for conversation-derived facts, `article` for externally-fetched reference material. A single SQLite FTS5 index (`search.db` in `~/.local/share/co-cli/`) is derived from those files — it is rebuildable and never the source of truth.

```
recall_memory / recall_article / search_knowledge / search_notes
       │
       ▼
KnowledgeIndex.search(query, source, kind, tags, limit)
       │
       ├── FTS5 MATCH + bm25() → ranked results      (Phase 1, shipped)
       ├── + vec0 cosine similarity → hybrid merge    (Phase 2, shipped — see Config)
       └── + cross-encoder rerank                     (Phase 3, see TODO)
       │
  search.db  (~/.local/share/co-cli/search.db)
```

## 2. Core Logic

### Conceptual Model

```
All knowledge items = markdown files with YAML frontmatter
  kind: memory   → conversation-derived (preference, correction, decision, context, pattern)
  kind: article  → externally-fetched (web docs, reference material, research)

Directory: .co-cli/knowledge/*.md           (unified flat dir)
Assets:    .co-cli/knowledge/assets/{slug}/ (multimodal, binary — referenced by articles)
Index:     ~/.local/share/co-cli/search.db  (derived, rebuildable)
```

No short/long-term memory tiers. Instead, three mechanisms serve as the tiering:
- **Gravity**: `updated` timestamp refreshed on recall — frequently recalled items surface first
- **Decay**: oldest unprotected memories removed when `memory_max_count` exceeded (articles are decay-protected by default)
- **`personality-context` tag**: items tagged `personality-context` are injected into the system prompt every turn via `add_personality_memories`

### Frontmatter Schema

```yaml
# Required
id: int
created: ISO8601

# Kind and provenance
kind: memory | article        # defaults to "memory" on parse if absent
origin_url: str | null        # source URL for articles; null for memories

# provenance — lifecycle field: how/why this item was created
# Named "provenance" to avoid collision with KnowledgeIndex.source column
# (which is a storage-namespace concept: "memory", "obsidian", "drive")
provenance: detected | user-told | planted | auto_decay | web-fetch

# Optional
updated: ISO8601 | null
tags: list[str]
auto_category: str | null
decay_protected: bool
related: list[str]
```

`provenance` values: `detected` = auto-saved by signal detector, `user-told` = explicit `save_memory` call, `planted` = pre-seeded character base memories, `auto_decay` = created by decay consolidation, `web-fetch` = saved via `save_article`.

`provenance` is the lifecycle field. The `source` column in `KnowledgeIndex` is a storage-namespace concept (`'memory'`, `'obsidian'`, `'drive'`) — distinct from `provenance`.

### Storage Layout

```
.co-cli/knowledge/
  001-user-prefers-pytest.md       kind: memory
  002-python-asyncio-guide.md      kind: article, origin_url: https://...
  003-kyle-mccloskey-collab.md     kind: memory
  assets/
    python-asyncio-guide/
      diagram.png

~/.local/share/co-cli/
  search.db                        FTS5 index (rebuildable)
  co-cli.db                        Telemetry (separate, unchanged)
```

`assets/` is the one allowed subdir — binary files referenced by articles, kept separate so `glob("*.md")` scans remain clean.

### KnowledgeIndex Design Principle

`KnowledgeIndex` is a single SQLite-backed search index (`search.db`) that any source can write to. The `source` column (`'memory'`, `'obsidian'`, `'drive'`) is the storage-namespace discriminator — all knowledge files (memories + articles) share `source='memory'`; `kind` distinguishes them within that namespace. External sources (Obsidian, Drive) get indexed when tools read them; there is no background crawler.

Startup sync runs hash-based change detection against `.co-cli/knowledge/*.md` files to keep the index current. `search.db` can be deleted and rebuilt cleanly on next startup. When `search.db` is absent or `knowledge_search_backend="grep"`, all search tools fall back to grep-based scan of `.co-cli/knowledge/*.md`.

**Indexing triggers by source:**

| Source | Index trigger | Notes |
|--------|--------------|-------|
| Memory | `save_memory()` + startup sync | Frontmatter fields (tags, category) indexed |
| Article | `save_article()` + startup sync | Decay-protected by default |
| Obsidian | On `search_notes()` call or `search_knowledge()` with `source=None/obsidian`, hash-based incremental | Local markdown vault |
| Drive | On `read_drive_file()` — text available after full fetch | Cached locally |

### Tool Surface

| Tool | Backed by | Notes |
|------|-----------|-------|
| `save_memory(content, tags)` | Writes `kind: memory` file | Dedup-on-write, decay check |
| `recall_memory(query, tags?, created_after?, created_before?)` | `search(query, source="memory")` | Tag + temporal filtering supported |
| `update_memory(slug, old_content, new_content)` | In-place edit by slug | Guards: line-prefix rejection, uniqueness |
| `append_memory(slug, content)` | Append to existing slug | Raises on missing slug |
| `list_memories(kind?)` | Filesystem scan | `kind` filter supported |
| `save_article(content, title, origin_url, tags?)` | Writes `kind: article` file | URL-dedup consolidation |
| `recall_article(query, tags?, created_after?, created_before?)` | `search(query, kind="article")` | Summary-only return |
| `read_article_detail(slug)` | Direct file read | Progressive loading for large articles |
| `search_knowledge(query, source?, kind?)` | `search(query)` | Cross-source: memories + articles + notes + drive |

### FTS5 Schema

```sql
CREATE TABLE IF NOT EXISTS docs (
    source   TEXT NOT NULL,           -- 'memory', 'obsidian', 'drive'
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

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title,
    content,
    tags,
    tokenize='porter unicode61',
    content='docs',
    content_rowid='rowid'
);
```

Sync triggers keep `docs_fts` in lock-step with `docs` on insert, delete, and update. Tag filtering uses Python-side set matching on the space-separated `tags` column returned by FTS5 — exact, no false positives.

### Evolution Path

| Step | What ships | Search quality | New deps |
|------|-----------|---------------|----------|
| Prereq A | Flat dir migration | Grep (unchanged) | None |
| Prereq B | Articles + tools | Grep on both kinds | None |
| Phase 1 | FTS5 index | BM25 ranked | None |
| Phase 2 | Hybrid search | Semantic + keyword | sqlite-vec |
| Phase 3 | Reranker | Cross-encoder | llama-cpp-python |

Prereq A, Prereq B, Phase 1, and Phase 2 are shipped. Phase 2 is code-complete: `KnowledgeIndex` implements `_hybrid_search()`, `_vec_search()`, `_hybrid_merge()`, `_embed_cached()`, and `_generate_embedding()`. Phase 2 activates when `knowledge_search_backend="hybrid"` (see Config). Known gaps and open bugs blocking ship-ready status are tracked in `TODO-sqlite-tag-fts-sem-search-for-knowledge.md`. Phase 3 is not yet started.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `knowledge_search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `"fts5"` | `"grep"` (legacy fallback), `"fts5"` (BM25 only), `"hybrid"` (Phase 2: BM25 + vector) |
| `knowledge_embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `"ollama"` | Embedding provider for Phase 2 hybrid search: `"ollama"`, `"gemini"`, or `"none"` |
| `knowledge_embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `"embeddinggemma"` | Model name passed to the embedding provider |
| `knowledge_embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` | `256` | Embedding vector dimensions (must match the model's output size) |
| `knowledge_hybrid_vector_weight` | (no env var) | `0.7` | Weight applied to vector similarity scores in the hybrid merge (code gap: not in `fill_from_env` map) |
| `knowledge_hybrid_text_weight` | (no env var) | `0.3` | Weight applied to BM25 text scores in the hybrid merge (code gap: not in `fill_from_env` map) |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/knowledge_index.py` | `KnowledgeIndex` class — FTS5 schema, sync triggers, `search()`, `index()`, `remove_stale()`, `rebuild()` |
| `co_cli/tools/memory.py` | `save_memory`, `recall_memory`, `update_memory`, `append_memory`, `list_memories` — memory tools, `_save_memory_impl` shared write path |
| `co_cli/tools/articles.py` | `save_article`, `recall_article`, `read_article_detail`, `search_knowledge` — article tools and cross-source search |
| `co_cli/tools/obsidian.py` | `search_notes` — indexes Obsidian vault on first call, delegates to `KnowledgeIndex` |
| `co_cli/tools/google_drive.py` | `read_drive_file` — indexes Drive content into `KnowledgeIndex` on fetch |
| `co_cli/_frontmatter.py` | Frontmatter parse/validate — `kind`, `provenance`, `origin_url` fields; `validate_memory_frontmatter()` |
| `.co-cli/knowledge/` | Flat knowledge store — all `kind: memory` and `kind: article` files |
