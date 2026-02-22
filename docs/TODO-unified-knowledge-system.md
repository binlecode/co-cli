# TODO: Unified Knowledge System (Memory + Articles + FTS5)

## Status Legend
- `[ ]` pending
- `[~]` in progress
- `[x]` done

---

## Context

The current memory system uses `.co-cli/knowledge/memories/` for conversation-derived facts and had a planned second tier for externally-fetched "articles" with separate storage, separate tools, and separate search. Three identified improvements:

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
.co-cli/knowledge/               ← unified flat dir (NEW — was memories/ subdir)
  001-user-prefers-pytest.md       kind: memory
  002-python-asyncio-guide.md      kind: article, origin_url: https://...
  003-kyle-mccloskey-collab.md     kind: memory

~/.local/share/co-cli/
  search.db                        FTS5 index (DATA_DIR, existing path)
  co-cli.db                        Telemetry (existing, unchanged)
```

---

## Implementation Steps

### Step 1 — `co_cli/knowledge_index.py` (new file)
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

### Step 2 — `co_cli/_frontmatter.py` (modify)
- [ ] Add optional `kind` field validation in `validate_memory_frontmatter()`: must be `"memory"` or `"article"` if present
- [ ] Add optional `origin_url` field validation: string or null if present
- [ ] Ensure backward compat: files without `kind` parse/validate without error

### Step 3 — `co_cli/config.py` and `co_cli/deps.py` (modify)
- [ ] `config.py`: add `knowledge_search_backend: Literal["fts5", "grep"] = Field(default="fts5")` with env var `CO_KNOWLEDGE_SEARCH_BACKEND`
- [ ] `deps.py`: add `knowledge_index: Any | None = field(default=None, repr=False)` (`Any` avoids circular import)
- [ ] `deps.py`: add `knowledge_search_backend: str = "fts5"`

### Step 4 — `co_cli/tools/memory.py` (modify)
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

### Step 5 — `co_cli/tools/personality.py` (modify)
- [ ] Update `memory_dir` path from `.co-cli/knowledge/memories` → `.co-cli/knowledge`

### Step 6 — `co_cli/main.py` (modify)
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

### Step 7 — `co_cli/agent.py` (modify)
- [ ] Import `save_article` from `co_cli.tools.memory`
- [ ] Register: `agent.tool(save_article, requires_approval=True)`

### Step 8 — Tests

#### `tests/test_knowledge_index.py` (new file)
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

#### `tests/test_memory.py` (modify)
- [ ] Update all paths from `memories/` → `knowledge/`
- [ ] Add `kind: memory` assertions in save/recall tests
- [ ] Add FTS5 round-trip test: save memory → recall via FTS → verify result appears

#### `tests/test_memory_decay.py` (modify)
- [ ] Update seed paths from `memories/` → `knowledge/`

#### `tests/test_personality_tools.py` (modify)
- [ ] Update `memory_dir` paths from `memories/` → `knowledge/`

#### `tests/test_save_article.py` (new file)
- [ ] Test `save_article` writes file with correct frontmatter (`kind: article`, `origin_url`, `source: web-fetch`)
- [ ] Test `save_article` dedup: saving same `origin_url` twice → `action: consolidated`
- [ ] Test `recall_memory` returns article results (kind: article items are searchable)
- [ ] Test `list_memories` with `kind="article"` filter returns only articles
- [ ] Test `list_memories` with `kind="memory"` filter returns only memories

---

## Verification Checklist

- [ ] `uv run pytest tests/test_memory.py -v` — existing memory tests stay green (path migration tested here)
- [ ] `uv run pytest tests/test_knowledge_index.py -v` — new knowledge index unit tests pass
- [ ] `uv run pytest tests/test_save_article.py -v` — new article save tests pass
- [ ] `uv run pytest tests/test_personality_tools.py -v` — personality path update smoke test passes
- [ ] `uv run pytest tests/test_memory_decay.py -v` — decay tests pass with updated paths
- [ ] `uv run co status` — smoke test: agent starts, memory dir migrated, index created
- [ ] `uv run python evals/eval_memory_proactive_recall.py` — integration eval passes

---

## Files Summary

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
| `tests/test_knowledge_index.py` | **Create** | FTS5 index/search/sync/rebuild tests |
| `tests/test_memory.py` | **Modify** | update paths, add kind assertions, add FTS5 round-trip test |
| `tests/test_memory_decay.py` | **Modify** | update seed paths memories/ → knowledge/ |
| `tests/test_personality_tools.py` | **Modify** | update memory_dir paths |
| `tests/test_save_article.py` | **Create** | save_article round-trip, URL dedup, recall integration |
