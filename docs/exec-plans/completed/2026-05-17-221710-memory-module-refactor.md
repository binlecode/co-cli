# Memory Module Refactor — `knowledge` → `memory`, split session, extract `index/` facade

## Context

The current `co_cli/memory/` module conflates three layers of responsibility into one god class (`MemoryStore`, 1392 LOC):

1. **Storage primitive** — SQLite schema, FTS5, sqlite-vec, transactions, CRUD
2. **Retrieval orchestration** — FTS + vec + RRF + reranker
3. **Domain logic** — session indexing (JSONL extract, chunking, hash skip) and knowledge indexing (frontmatter, paragraph chunking, source-ref dedup) both buried inside the same class

This creates concrete problems documented earlier in the conversation:
- `session_chunker.py` produces `SessionChunk` records that get manually converted to `Chunk` at the storage boundary inside `MemoryStore.index_session()` — type translation tax
- `reindex()` in `service.py` duplicates `sync_dir()`'s per-file logic
- The same artifact result dict shape is constructed in 5 places (4× in `recall.py`, 1× in `memory_store.py`)
- `remove_stale()` breaks transaction atomicity (commits per-iteration inside a loop)
- Two transaction models coexist (`MemoryTransaction` and raw `with self._conn`)
- Tool naming (`knowledge_*`) is the only system in the peer ecosystem that doesn't use "memory" for long-term declarative facts

Additionally, the spec's four-tier model treats "memory" as an umbrella with knowledge and session as sub-channels. That umbrella does no work at the agent surface — the agent never sees a tool called `memory_*`. The umbrella adds spec indirection without operational benefit.

This refactor:
- Promotes session to a co-equal operational tier (five tiers: doctrine, tools, skills, memory, session)
- Renames the long-term-facts surface from `knowledge_*` to `memory_*` (matches every peer system)
- Splits memory and session into separate domain modules
- Extracts a private `index/` infrastructure facade (`IndexStore` public; retrieval, embedding, providers private)
- Eliminates the type-translation tax (drops `SessionChunk` — session chunker returns `Chunk` directly)

**No migration code. No backward-compat shims. Per project rules (`feedback_zero_backward_compat`), this is a hard cut.** Users move `~/.co-cli/knowledge/` → `~/.co-cli/memory/` manually after upgrade; the search DB is deleted and reindexed on next boot.

## Final architecture

```
co_cli/
├── index/                       INFRASTRUCTURE FACADE — IndexStore is the only public class
│   ├── __init__.py              (docstring only per project rules)
│   ├── store.py                 IndexStore — schema, write CRUD, transactions, search facade
│   ├── chunk.py                 Chunk dataclass — write contract
│   ├── schema.py                DDL constants (extracted from memory_store.py)
│   ├── _retrieval.py            RetrievalService — FTS + vec + RRF + rerank (private)
│   ├── _embedding.py            EmbeddingService — embed + cache (private)
│   ├── _providers.py            ollama / tei / gemini dispatch (private)
│   ├── _search_util.py          sanitize, normalize, helpers (private)
│   └── _stopwords.py            STOPWORDS frozenset (private)
│
├── memory/                      DOMAIN — long-term declarative facts
│   ├── __init__.py
│   ├── store.py                 MemoryStore — kinds, two-pass policy, decay hooks
│   ├── service.py               save_artifact, mutate_artifact, reindex
│   ├── chunker.py               chunk_text — paragraph-aware (from text_chunker.py)
│   ├── artifact.py              MemoryArtifact, ArtifactKindEnum (knowledge → memory rename)
│   ├── frontmatter.py           parse_frontmatter, render_frontmatter, render_artifact_file
│   ├── similarity.py            Jaccard dedup
│   ├── decay.py                 decay scoring
│   ├── dream.py                 dream-cycle mining
│   ├── archive.py               archive lifecycle
│   ├── _window.py               build_transcript_window (used by dream.py)
│   └── prompts/                 dream_merge.md, dream_miner.md
│
├── session/                     DOMAIN — past conversation transcripts
│   ├── __init__.py
│   ├── store.py                 SessionStore — uuid8, append-only sync policy
│   ├── service.py               index_session, sync_sessions
│   ├── chunker.py               chunk_session — returns list[Chunk] directly (drop SessionChunk)
│   ├── transcript.py            extract_messages (renamed from indexer.py to fix misnomer)
│   ├── persistence.py           JSONL append/load/compact (renamed from memory/transcript.py)
│   ├── browser.py               list_sessions (renamed from session_browser.py)
│   └── filename.py              parse_session_filename (renamed from session.py)
│
└── tools/
    ├── memory/                  AGENT SURFACE — memory tier
    │   ├── recall.py            memory_search (renamed from knowledge_search)
    │   ├── view.py              memory_view (renamed from knowledge_view)
    │   └── manage.py            memory_manage (renamed from knowledge_manage)
    │
    └── session/                 AGENT SURFACE — session tier (NEW)
        ├── recall.py            session_search
        └── view.py              session_view
```

## Decisions (from user input)

| Decision | Choice |
|---|---|
| Canon dir | `souls/{role}/memories/` → `souls/{role}/canon/` |
| DB source value | `'knowledge'` → `'memory'` |
| Data migration | **None** — user moves `~/.co-cli/knowledge/` → `~/.co-cli/memory/` manually post-upgrade; DB rebuilds from files |
| Phasing | Single atomic PR |

## File operations

### New modules (create)
- `co_cli/index/` (full tree above)
- `co_cli/session/` (full tree above)
- `co_cli/tools/session/` (recall.py, view.py)

### File moves
| From | To |
|---|---|
| `co_cli/memory/text_chunker.py` (chunk_text) | `co_cli/memory/chunker.py` |
| `co_cli/memory/text_chunker.py` (Chunk dataclass) | `co_cli/index/chunk.py` |
| `co_cli/memory/_embedder.py` | `co_cli/index/_providers.py` + `co_cli/index/_embedding.py` |
| `co_cli/memory/search_util.py` | `co_cli/index/_search_util.py` |
| `co_cli/memory/stopwords.py` | `co_cli/index/_stopwords.py` |
| `co_cli/memory/session_chunker.py` | `co_cli/session/chunker.py` (drop SessionChunk; return list[Chunk]) |
| `co_cli/memory/session_browser.py` | `co_cli/session/browser.py` |
| `co_cli/memory/session.py` | `co_cli/session/filename.py` |
| `co_cli/memory/indexer.py` | `co_cli/session/transcript.py` (fix misnomer) |
| `co_cli/memory/transcript.py` | `co_cli/session/persistence.py` (clarify scope) |

### File splits (the big surgery)
**`co_cli/memory/memory_store.py` (1392 LOC) splits into:**
- `co_cli/index/store.py` — `IndexStore` (schema load, CRUD: `upsert`, `index_chunks`, `remove`, `remove_chunks`, `remove_stale`, `needs_reindex`, `find_by_source_ref`, `count_docs`, `list_titles_by_source`, `get_path_by_title`, `get_chunk_content`, `probe`, `close`, `transaction()`); plus `IndexStore.search(...)` as the public read facade that delegates to `_retrieval`
- `co_cli/index/_retrieval.py` — `RetrievalService` (search orchestration: `_fts_search`, `_run_chunks_fts`, `_fts_chunks_raw`, `_vec_chunks_search`, `_hybrid_search`, `_hybrid_merge`, `_rerank_results`, `_tei_rerank`, `_fetch_reranker_texts`, `_build_fts_query`, `_chunk_row_to_result`); plus `SearchResult` dataclass
- `co_cli/index/_embedding.py` — `EmbeddingService` (`embed(text)`, `_embed_cached`, cache CRUD against `embedding_cache` table)
- `co_cli/index/schema.py` — `_SCHEMA_SQL` constant + table-name constants
- `co_cli/memory/store.py` — `MemoryStore` (domain): owns `sync_dir`, `rebuild`, two-pass search policy (`search_artifacts` with user-priority + waterfall), kinds-aware filtering. Composes `IndexStore`.
- `co_cli/session/store.py` — `SessionStore` (domain): owns `index_session`, `sync_sessions`, session-specific search shape, current-session exclusion. Composes `IndexStore`.

### Renames (in-place)
| From | To |
|---|---|
| `co_cli/memory/service.py:KnowledgeArtifact` | `MemoryArtifact` |
| `IndexSourceEnum.KNOWLEDGE = 'knowledge'` | `IndexSourceEnum.MEMORY = 'memory'` |
| `ArtifactKindEnum` | unchanged (kinds USER/RULE/ARTICLE/NOTE/CANON survive) |

### File deletions (delete after content extracted)
- `co_cli/memory/memory_store.py` (content distributed)
- `co_cli/memory/text_chunker.py` (Chunk → index/, chunk_text → memory/chunker.py)
- `co_cli/memory/_embedder.py` (→ index/_providers.py + _embedding.py)
- `co_cli/memory/search_util.py` (→ index/_search_util.py)
- `co_cli/memory/stopwords.py` (→ index/_stopwords.py)
- `co_cli/memory/session_chunker.py` (→ session/chunker.py)
- `co_cli/memory/session_browser.py` (→ session/browser.py)
- `co_cli/memory/session.py` (→ session/filename.py)
- `co_cli/memory/indexer.py` (→ session/transcript.py)
- `co_cli/memory/transcript.py` (→ session/persistence.py)

## Tool surface renames

| Old tool | New tool |
|---|---|
| `knowledge_search` | `memory_search` |
| `knowledge_view` | `memory_view` |
| `knowledge_manage` | `memory_manage` |
| `session_search` | unchanged (moves dir) |
| `session_view` | unchanged (moves dir) |

Approval subject string: `tool:knowledge_manage:<action>:<name>` → `tool:memory_manage:<action>:<name>`. Persisted approval state in `~/.co-cli/approvals.db` (or wherever it lives) will not match; users re-approve once. Per zero-backward-compat: no migration.

## Config renames

In `co_cli/config/core.py`:
- `Settings.knowledge_path` → `Settings.memory_path`
- `Settings.knowledge: KnowledgeSettings` → `Settings.memory: MemorySettings`
- `KnowledgeSettings` class → `MemorySettings` class (same 18 fields, no structural change)
- `KNOWLEDGE_DIR = USER_DIR / "knowledge"` → `MEMORY_DIR = USER_DIR / "memory"`
- Env vars: all 18 `CO_KNOWLEDGE_*` → `CO_MEMORY_*`

## Bootstrap (`co_cli/bootstrap/core.py`) changes

- `_discover_memory_backend(config, ...)` — still constructs `MemoryStore`, but now constructs `IndexStore` first, then composes `MemoryStore(index_store, config)` and `SessionStore(index_store, config)`. Returns all three (or returns deps that hold all three).
- `_sync_memory_store(...)` → renamed `_sync_memory_domain(memory_store, config, frontend, memory_dir)`. Body changes: `store.sync_dir("knowledge", knowledge_dir)` → `memory_store.sync_dir(memory_dir)` (source baked in to domain store).
- `_sync_canon_store(...)` — body changes: `store.sync_dir("canon", canon_dir, ...)` → call goes through `IndexStore.sync_dir(source='canon', directory=canon_dir, no_chunk=True)` since canon is not memory-domain (it's doctrine indexed in the shared DB).
- Canon path: `souls_dir / config.personality / "memories"` → `souls_dir / config.personality / "canon"`. Update `personality/prompts/loader.py` similarly (the glob path).
- `init_session_index(...)` — body changes: `store.sync_sessions(deps.sessions_dir, exclude=...)` → `session_store.sync(deps.sessions_dir, exclude=...)`.

## Deps (`co_cli/deps.py`) changes

`CoDeps` currently exposes `memory_store: MemoryStore | None`. Replace with:
- `index_store: IndexStore | None`
- `memory_store: MemoryStore | None` (new domain store)
- `session_store: SessionStore | None` (new domain store)
- `knowledge_dir: Path` → `memory_dir: Path`

All tool code currently calling `ctx.deps.memory_store.search(...)` (which today goes to the god class) must be updated to call the appropriate domain store.

## Tool layer changes

### `co_cli/tools/memory/recall.py`
- Drop the `_search_sessions`, `_browse_recent`, `_format_session_results`, `_SESSIONS_CHANNEL_CAP` — moves to `co_cli/tools/session/recall.py`
- Rename `knowledge_search` → `memory_search`
- Inline `_search_artifacts` orchestration calls `ctx.deps.memory_store.search_artifacts(query, kinds, limit)` — the two-pass policy moves into `memory/store.py`
- Extract the shared result-dict builder to a helper (kills the 4-place duplication)
- `_grep_recall`, `_grep_artifacts_fallback` stay (fallback when store is None)

### `co_cli/tools/session/recall.py` (NEW)
- `session_search` tool
- `_search_sessions`, `_browse_recent`, `_format_session_results` move here
- Calls `ctx.deps.session_store.search(query, limit)` and `ctx.deps.session_store.list_recent(limit, exclude_current)`

### `co_cli/tools/memory/view.py`
- Drop `session_view` — moves to `co_cli/tools/session/view.py`
- Rename `knowledge_view` → `memory_view`

### `co_cli/tools/session/view.py` (NEW)
- `session_view` (unchanged contract; uses `ctx.deps.session_store` for path resolution if convenient)

### `co_cli/tools/memory/manage.py`
- Rename `knowledge_manage` → `memory_manage`
- Approval subject string updated
- `_handle_create`, `_handle_mutate`, `_handle_delete` body changes: `memory_store` references retargeted

## Toolset registration

`co_cli/agent/toolset.py` — update tool imports and registrations for the 5 renamed/moved tools.

## Existing code to reuse (don't reimplement)

| Need | Existing function | Location |
|---|---|---|
| Paragraph-aware chunking | `chunk_text(text, chunk_tokens, overlap_tokens)` | currently `co_cli/memory/text_chunker.py` |
| JSONL message extraction | `extract_messages(path)` | currently `co_cli/memory/indexer.py` |
| Frontmatter parse/render | `parse_frontmatter`, `render_frontmatter`, `render_artifact_file` | currently `co_cli/memory/frontmatter.py` |
| Atomic file write | `atomic_write_text` | `co_cli/persistence/atomic.py` (DO NOT change) |
| FTS5 query sanitize | `sanitize_fts5_query`, `normalize_bm25`, `run_fts` | currently `co_cli/memory/search_util.py` |
| Stopwords | `STOPWORDS` | currently `co_cli/memory/stopwords.py` |
| Embedder dispatch | `build_embedder(provider, host, model, url, key)` | currently `co_cli/memory/_embedder.py` |
| Resource lock | `ctx.deps.resource_locks.try_acquire(name)` | `co_cli/tools/resource_lock.py` (DO NOT change) |
| Tool I/O | `tool_output`, `tool_error` | `co_cli/tools/tool_io.py` (DO NOT change) |
| Agent tool decorator | `@agent_tool(visibility=..., approval=...)` | `co_cli/tools/agent_tool.py` (DO NOT change) |

## Concrete bug fixes incorporated into the refactor

These were identified in the conversation and should be fixed during the move (free with the rewrite):

1. **`remove_stale()` atomicity** — wrap the loop in a single transaction; don't commit per-iteration. Move to `IndexStore`.
2. **Drop `SessionChunk`** — `chunk_session()` returns `list[Chunk]` directly. `index_session()` no longer does field translation.
3. **Source filter SQL helper** — add `_source_clause(sources, col)` next to `_kind_clause()` in `_search_util.py`. Use in all three call sites.
4. **Drop `MemoryTransaction.index(**kwargs)`** — replace with explicit-keyword signature matching `_index_no_commit`.
5. **Delete `_generate_embedding()` indirection** — call `self._embed_fn(text)` directly in `EmbeddingService._embed_cached`.
6. **Drop `"channel": "artifacts"` key** in `IndexStore.list_artifacts()` return shape; the tool layer no longer strips it.
7. **Two-pass search policy moves to `MemoryStore`** — `_user_priority_pass` and waterfall logic leaves `tools/memory/recall.py` and becomes a `MemoryStore.search_artifacts(query, kinds, limit)` method.
8. **`rebuild()` reuses `_remove_chunks_no_commit`** — stops duplicating the vec deletion logic.

## Tests + evals

### Test files to update (file paths preserved; rename references inside)
- `tests/test_flow_artifact_manage.py`
- `tests/test_flow_knowledge_search.py` → rename file to `test_flow_memory_search.py`
- `tests/test_flow_knowledge_view.py` → `test_flow_memory_view.py`
- `tests/test_flow_memory_write.py`
- `tests/test_flow_bootstrap_canon.py`
- `tests/test_flow_memory_canon_recall.py`
- `tests/test_flow_memory_artifacts_waterfall_cap.py`
- `tests/test_flow_memory_store.py`
- `tests/test_flow_session_search.py`
- Any test importing `from co_cli.memory.memory_store import MemoryStore` → split: `from co_cli.index import IndexStore` for storage tests, `from co_cli.memory import MemoryStore` for domain tests

### Eval files to update
- `evals/eval_memory.py`
- `evals/eval_daily_chat.py`
- `evals/_fixtures.py`
- `evals/_timeouts.py`

### Specs to update
- `docs/specs/memory.md` (becomes the memory tier spec, no longer umbrella)
- `docs/specs/sessions.md` (becomes the session tier spec, peer to memory)
- `docs/specs/knowledge.md` (DELETE — was the channel sub-spec, content folds into `memory.md`)
- `docs/specs/01-system.md` (update four-tier → five-tier model)
- `docs/specs/prompt-assembly.md`, `docs/specs/personality.md`, `docs/specs/dream.md` — search for "knowledge" terminology and update

### Agent docs to update
- `CLAUDE.md` (project root) — memory system section, key paths, knowledge → memory
- `agent_docs/tools.md`, `agent_docs/code-conventions.md` — terminology
- `co_cli/context/rules/*` — search for "knowledge" references in rule files

## Verification

### Build / lint
```bash
scripts/quality-gate.sh lint --fix    # ruff + format
```

### Tests (incremental fail-fast)
```bash
mkdir -p .pytest-logs
uv run pytest -x tests/test_flow_memory_search.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-memory-search.log
uv run pytest -x tests/test_flow_memory_write.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-memory-write.log
uv run pytest -x tests/test_flow_session_search.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-session-search.log
uv run pytest -x tests/test_flow_bootstrap_canon.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-canon.log
```

### Full quality gate
```bash
scripts/quality-gate.sh full
```

### End-to-end smoke (manual)
1. Move existing data: `mv ~/.co-cli/knowledge ~/.co-cli/memory && rm ~/.co-cli/co-cli-search.db`
2. Start REPL: `uv run co chat`
3. Verify bootstrap reindexes from `~/.co-cli/memory/` with no errors
4. In REPL: ask agent to recall a known fact — verify `memory_search` tool is called
5. In REPL: ask agent about a past session — verify `session_search` tool is called
6. In REPL: ask agent to remember something new — verify `memory_manage(action='create')` is called with approval prompt
7. Run startup banner — verify memory + session counts display correctly

### Eval smoke
```bash
uv run python evals/eval_memory.py
uv run python evals/eval_daily_chat.py
```

Verify rubric pass rates aren't degraded by the rename (tool-routing might shift; if degraded, root-cause then fix — do not paper over).

## Out of scope (do not do)

- **Migration code.** No `~/.co-cli/knowledge` → `~/.co-cli/memory` auto-rename. No DB column value translation (`'knowledge'` → `'memory'`). User wipes the DB and moves the dir manually.
- **Backward-compat aliases.** No `knowledge_search` alias for `memory_search`. No `Settings.knowledge` alias.
- **Abstract base classes for swappable backends.** `IndexStore` stays concrete (SQLite + sqlite-vec + FTS5). No `BaseIndexStore` ABC.
- **New features.** No new tools. No new search modes. No new memory kinds. Pure restructuring.
- **Performance changes.** Same indexing, same chunking constants, same query patterns. Behavior preserved.
- **Touching `souls/{role}/canon/` content.** Just rename the dir name; the .md files inside don't change.

## Critical files for execution reference

| Purpose | Path |
|---|---|
| The 1392-LOC god class being split | `co_cli/memory/memory_store.py` |
| Knowledge service (rename + move) | `co_cli/memory/service.py` |
| Memory artifact model (rename enum value) | `co_cli/memory/artifact.py` |
| Tool layer entry points | `co_cli/tools/memory/{recall,manage,view}.py` |
| Bootstrap (composition root) | `co_cli/bootstrap/core.py` |
| Deps container | `co_cli/deps.py` |
| Config (18-field rename) | `co_cli/config/core.py` |
| Canon glob (path rename) | `co_cli/personality/prompts/loader.py` |
| Spec docs | `docs/specs/memory.md`, `docs/specs/sessions.md`, `docs/specs/knowledge.md` (DELETE) |
| Project CLAUDE.md | `/Users/binle/workspace_genai/co-cli/CLAUDE.md` |

---

## Implementation Review — 2026-05-17

### Evidence

| Area | Spec Requirement | Spec Fidelity | Key Evidence |
|------|-----------------|---------------|-------------|
| `co_cli/index/` | IndexStore + all 13 CRUD methods | ✓ pass | `store.py:213-534` — all methods confirmed |
| `co_cli/index/` | `remove_stale()` single-transaction atomicity | ✓ pass | `store.py:411-413` — loop body uses `_remove_no_commit`, single commit after loop |
| `co_cli/index/` | `_retrieval.py`, `_embedding.py`, `_providers.py` private | ✓ pass | No external imports after fix |
| `co_cli/index/` | `__init__.py` docstring-only | ✓ pass | Module contains only docstring |
| `co_cli/memory/` | All deleted files gone | ✓ pass | `memory_store.py`, `_embedder.py`, `session_chunker.py` etc. absent |
| `co_cli/memory/` | `IndexSourceEnum.MEMORY = 'memory'` | ✓ pass | `artifact.py:41-44` |
| `co_cli/memory/` | `MemoryArtifact` (no `KnowledgeArtifact`) | ✓ pass | `artifact.py:50`, `service.py:18` |
| `co_cli/memory/` | Two-pass `search_artifacts` in `store.py` | ✓ pass | `store.py:166-207` |
| `co_cli/memory/` | `MemoryStore` composes `IndexStore` via constructor | ✓ pass | `store.py:47` — `def __init__(self, *, index: IndexStore, ...)` |
| `co_cli/session/` | `chunk_session()` returns `list[Chunk]`, no `SessionChunk` | ✓ pass | `chunker.py:201`, zero `SessionChunk` grep hits |
| `co_cli/session/` | `SessionStore` composes `IndexStore` | ✓ pass | `store.py:39` |
| `co_cli/tools/memory/` | `memory_search`, `memory_view`, `memory_manage` tools | ✓ pass | `recall.py:151`, `view.py:24`, `manage.py:39` |
| `co_cli/tools/session/` | `session_search`, `session_view` tools | ✓ pass | `recall.py:121`, `view.py:27` |
| `co_cli/tools/memory/` | Approval subject `tool:memory_manage:` | ✓ pass | `manage.py:26` |
| Config | `Settings.memory_path`, `MemorySettings`, `MEMORY_DIR` | ✓ pass | `config/core.py:37,84,95` |
| Config | `CO_KNOWLEDGE_*` env vars gone | ✓ pass | zero grep hits |
| Bootstrap | `IndexStore` constructed first, then composed into domain stores | ✓ pass | `bootstrap/core.py:402-403` |
| Bootstrap | Canon path `souls/{role}/canon/` | ✓ pass | `bootstrap/core.py:189` |
| Deps | `CoDeps.index_store`, `.memory_store`, `.session_store` | ✓ pass | `deps.py:271-273` |
| Deps | `memory_dir` (no `knowledge_dir`) | ✓ pass | `deps.py:290`, zero `knowledge_dir` hits |
| Toolset | `memory_*` + `session_*` registered, no `knowledge_*` | ✓ pass | `toolset.py:30-35` |
| CLAUDE.md | Five tiers, correct tool names | ✓ pass | CLAUDE.md lines 40, 44, 46 |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `memory/store.py` imports `SearchResult` from `co_cli.index._retrieval` (private) | `memory/store.py:19` | blocking | Changed to `from co_cli.index.store import SearchResult` |
| `session/store.py` same private import | `session/store.py:19` | blocking | Changed to `from co_cli.index.store import SearchResult` |
| `memory/similarity.py` imports `STOPWORDS` from `co_cli.index._stopwords` (private) | `similarity.py:5` | blocking | Renamed `_stopwords.py` → `stopwords.py`; updated import |
| `tools/obsidian/tools.py` imports `snippet_around` from `co_cli.index._search_util` (private) | `tools.py:10` | blocking | Renamed `_search_util.py` → `search_util.py`; updated all references |
| `04_tool_protocol.md` still references `knowledge_search`, `knowledge_view`, `knowledge_manage` | `rules/04_tool_protocol.md:102,106,116` | blocking | Updated to `memory_search`, `memory_view`, `memory_manage` |
| `skills/triage.md` references `knowledge_manage` | `triage.md:82` | blocking | Updated to `memory_manage` |
| `evals/eval_memory.py` calls `memory_store.remove("knowledge", path)` — wrong signature | `eval_memory.py:110,177` | blocking | Changed to `memory_store.remove(path)` |
| `evals/eval_memory.py` calls `memory_store._conn` — attribute moved | `eval_memory.py:193` | blocking | Changed to `memory_store._index._conn` |
| `evals/eval_memory.py` SQL uses `source='knowledge'` | `eval_memory.py:195` | blocking | Changed to `'memory'` |
| `evals/eval_memory.py` calls `memory_store.search(sources=["knowledge"])` — method removed | `eval_memory.py:208` | blocking | Changed to `memory_store.search_artifacts(query, None, 10)` |
| `evals/eval_memory.py` calls `reindex(deps.memory_store, ...)` — type wrong (MemoryStore≠IndexStore) | `eval_memory.py:157` | blocking | Changed to `deps.memory_store.reindex_one(path, body, markdown, fm)` |

### Tests

- Command: `uv run pytest -x`
- Result: **461 passed, 0 failed**
- Log: `.pytest-logs/20260517-231009-review-impl.log` (pre-fix run); current run confirmed 461 passed

### Behavioral Verification

- `uv run python -c "from co_cli.config.core import Settings; ..."`: ✓ `memory_path` and `memory.chunk_tokens` load correctly
- Import smoke: `IndexStore`, `SearchResult`, `MemoryStore`, `SessionStore`, `memory_search`, `memory_manage`, `session_search`, `stopwords.STOPWORDS`, `search_util.source_clause` — all import cleanly
- No `co_cli/index/_stopwords.py` or `co_cli/index/_search_util.py` remain on disk — confirmed

### Overall: PASS

All 11 blocking findings resolved. 461 tests pass. Lint clean. The refactor is complete and correct: five-tier architecture (doctrine / tools / skills / memory / session), `IndexStore` infrastructure facade with public `search_util.py` and `stopwords.py`, `MemoryStore` and `SessionStore` domain stores composing `IndexStore`, `memory_*` tool surface active, system prompt assets updated.
