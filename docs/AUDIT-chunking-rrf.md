# Delivery Audit: chunking-rrf

**Scope:** chunking-rrf
**Date:** 2026-03-10
**Verdict:** GAPS_FOUND
**Blocking:** 1
**Minor:** 1

---

## Phase 1 — Scope Resolution

Source files audited:

| File | Role |
|------|------|
| `co_cli/_chunker.py` | New module: `Chunk` dataclass + `chunk_text()` |
| `co_cli/_knowledge_index.py` | Modified: chunks schema, `index_chunks()`, `remove_chunks()`, FTS routing, RRF merge |
| `co_cli/tools/articles.py` | Modified: calls `index_chunks()` after `index()` on save and consolidate |
| `co_cli/tools/google_drive.py` | Modified: calls `index_chunks()` after `index()` in `read_drive_file` |
| `co_cli/config.py` | New settings: `knowledge_chunk_size`, `knowledge_chunk_overlap` |

DESIGN docs checked: all `docs/DESIGN-*.md`.

---

## Phase 2 — Feature Inventory

### A. New config settings

| Setting | Default | Env Var (source) |
|---------|---------|-----------------|
| `knowledge_chunk_size` | `600` | `CO_CLI_KNOWLEDGE_CHUNK_SIZE` |
| `knowledge_chunk_overlap` | `80` | `CO_CLI_KNOWLEDGE_CHUNK_OVERLAP` |

Both confirmed present in `Settings` Field definitions (lines 232–233 of `config.py`) and in `fill_from_env` `env_map` (lines 408–409).

### B. New / modified KnowledgeIndex methods

| Method | Description |
|--------|-------------|
| `index_chunks(source, doc_path, chunks)` | Writes chunk rows atomically; raises `ValueError` for source `"memory"` |
| `remove_chunks(source, path)` | Removes all chunk rows (and `chunks_vec` entries) for a path |
| `_fts_search()` | FTS routing: memory → `docs_fts`; non-memory → `chunks_fts` |
| `_vec_search()` | Vector routing: memory → `docs_vec`; non-memory → `chunks_vec` |
| `_hybrid_merge()` | Now uses Reciprocal Rank Fusion (RRF, k=60); `vector_weight`/`text_weight` ignored |

### C. New schema objects

| Object | Type | Key |
|--------|------|-----|
| `chunks` | Table | PRIMARY KEY `(source, doc_path, chunk_index)` |
| `chunks_fts` | FTS5 virtual table | indexes `content` from `chunks` |
| `chunks_vec` | sqlite-vec virtual table | keyed by chunk rowid; hybrid mode only |

All confirmed in `_SCHEMA_SQL` (lines 49–287 of `_knowledge_index.py`).

### D. FTS routing logic

- memory → `docs_fts` leg
- non-memory (library, obsidian, drive) → `chunks_fts` leg
- mixed / `None` scope → both legs, union by path, keep highest score

Confirmed in `_fts_search()` (line 569+) and `_run_chunks_fts()` (line 679+).

### E. RRF merge

`_hybrid_merge()` uses RRF (k=60, Cormack 2009). `vector_weight` and `text_weight` parameters retained in signature for backward compatibility but are explicitly ignored. Confirmed in source lines 912–930.

### F. Articles tool change

`save_article` and `_consolidate_article` paths both call `chunk_text()` then `index_chunks("library", ...)` after writing the docs row. Confirmed in `tools/articles.py` lines 375–383 (consolidate) and 431–438 (new save).

### G. Google Drive tool change

`read_drive_file` calls `chunk_text()` then `index_chunks("drive", file_id, ...)` after the docs `index()` call. Confirmed in `tools/google_drive.py` lines 169–176.

### H. `_chunker.py` module

`Chunk` dataclass: `index`, `content`, `start_line`, `end_line`. `chunk_text(text, chunk_size=512, overlap=64)` uses token estimation (`len/4`), paragraph > line > character split priority, and overlap prefix.

---

## Phase 3 — Coverage Check

### `docs/DESIGN-knowledge.md`

| Feature | Coverage Level | Notes |
|---------|---------------|-------|
| `_chunker.py` / `chunk_text()` | **Full** | Section 2.2 "Chunking" subsection documents `_chunker.chunk_text()`, split priority, token estimation. Files table row present. |
| `chunks` table schema | **Full** | Section 2.2 documents PRIMARY KEY `(source, doc_path, chunk_index)`, columns, and purpose. |
| `chunks_fts` virtual table | **Full** | Section 2.2 documents FTS5 on `content`, role in non-memory search. |
| `chunks_vec` virtual table | **Full** | Section 2.2 documents sqlite-vec for chunk embeddings in hybrid mode. |
| `index_chunks()` | **Full** | Section 2.2 describes atomicity, `ValueError` guard for source `"memory"`, and callers. Files table row present. |
| `remove_chunks()` | **Full** | Section 2.2 covers chunk row and `chunks_vec` cleanup. |
| FTS source routing (memory → `docs_fts`, non-memory → `chunks_fts`) | **Full** | Section 2.2 "FTS query behavior" source routing paragraph. |
| RRF merge (k=60) | **Full** | Section 2.2 "Scoring" paragraph describes RRF formula, `k=60`, Cormack 2009, and explicit note that `vector_weight`/`text_weight` are ignored. |
| `knowledge_chunk_size` | **Full** | Config table row 3.1: setting, env var `CO_CLI_KNOWLEDGE_CHUNK_SIZE`, default `600`, description. |
| `knowledge_chunk_overlap` | **Full** | Config table row 3.1: setting, env var `CO_CLI_KNOWLEDGE_CHUNK_OVERLAP`, default `80`, description. |
| articles.py: `index_chunks` after `index` | **Full** | Implicitly covered; Files table references `articles.py`. |
| google_drive.py: `index_chunks` after `index` | **Full** | Section 2.2 notes `remove_chunks` callers via sync_dir; Files table references `google_drive.py`. |

### `docs/DESIGN-knowledge.md`

| Feature | Coverage Level | Notes |
|---------|---------------|-------|
| `_chunker.py` | **Full** | Owning Code table (bottom) lists `_chunker.py` with description. |
| Article save → `index_chunks` call | **Full** | Part 3 write sequence step 3 documents `index_chunks("library", path, chunk_text(...))` explicitly. |
| `read_drive_file` → `index_chunks` call | **Full** | Part 7 documents `index_chunks("drive", file_id, chunk_text(...))` explicitly. |
| FTS source routing | **Full** | Part 5 Step 3a documents routing: memory sources → `docs_fts`, non-memory → `chunks_fts`. |
| RRF merge | **Full** | Part 5 Step 3a documents "merge via Reciprocal Rank Fusion (RRF, k=60): rank-based, not score-weighted". |
| Library sync → `index_chunks` | **Full** | Part 2 Step 1b explicitly notes chunk indexing for each changed library file. |
| Memory sync: no chunk indexing | **Full** | Part 2 Step 1a explicitly states "(memory source: no chunk indexing — chunks are for non-memory sources only)". |
| `knowledge_chunk_size` / `knowledge_chunk_overlap` | **Full** | Owning Code table row for `config.py` lists both settings. |

### `docs/DESIGN-index.md` (config reference table)

| Feature | Coverage Level | Notes |
|---------|---------------|-------|
| `knowledge_chunk_size` | **Full** | Line 176: setting, env var, default `600`, description "0 = disable chunking". |
| `knowledge_chunk_overlap` | **Full** | Line 177: setting, env var, default `80`, description. |
| `knowledge_hybrid_vector_weight` | **Partial** | Line 166: lists as "Hybrid retrieval vector-score weight". Does NOT note that the setting is now ignored — RRF replaced weighted scoring. Description is stale. |
| `knowledge_hybrid_text_weight` | **Partial** | Line 167: same staleness as above. |

### `docs/DESIGN-core.md` (CoConfig table)

| Feature | Coverage Level | Notes |
|---------|---------------|-------|
| `knowledge_chunk_size` | **Full** | Present in the CoConfig inline field list (line 216). |
| `knowledge_chunk_overlap` | **Full** | Present in the CoConfig inline field list (line 216). |

---

## Phase 4 — Second Pass

1. **All "full coverage" items confirmed**: Each item claimed as Full was verified to contain behavioral description, not just name mentions. Chunking section in `DESIGN-knowledge.md` describes token estimation, split priority, overlap, and schema semantics. FTS routing and RRF sections describe mechanisms, not just label them.

2. **Config settings in env_map**: Both `knowledge_chunk_size` (`CO_CLI_KNOWLEDGE_CHUNK_SIZE`) and `knowledge_chunk_overlap` (`CO_CLI_KNOWLEDGE_CHUNK_OVERLAP`) are present in `fill_from_env`'s `env_map`. Both env vars are documented correctly in all three doc locations that list them.

3. **`knowledge_hybrid_vector_weight` / `knowledge_hybrid_text_weight` in `DESIGN-index.md`**: These settings are documented in `DESIGN-knowledge.md` Section 3.1 correctly — they note "(none)" for the env var and explicitly state they are ignored in favor of RRF. However, `DESIGN-index.md` lines 166–167 describe them as "Hybrid retrieval vector-score weight" / "Hybrid retrieval BM25-score weight" with no note that they are now ignored. This is a stale description — inaccurate after the RRF change. Severity: **minor** (config setting, not an agent tool; behavior is correctly documented in the authoritative `DESIGN-knowledge.md`).

4. **Agent tools not in approval table**: No new agent tools were added in this delivery. `search_knowledge`, `save_article`, `recall_article`, `read_article_detail`, `search_drive_files`, `read_drive_file` pre-existed. The chunking-rrf delivery is infrastructure-only (no new tool registrations). No approval table gap applies.

5. **Blocking gap check**: `index_chunks()` and `remove_chunks()` are internal `KnowledgeIndex` methods, not agent-registered tools. They are not in DESIGN-tools.md (correct — they are engine internals). No blocking gap from tool coverage rules.

6. **One blocking gap found**: `DESIGN-system-bootstrap.md` — confirmed via grep: the bootstrap design doc has zero mentions of chunk indexing in startup sync. The startup sync sequence documented there for library files does not mention the `index_chunks()` call that was added. However, `DESIGN-knowledge.md` now owns the sync sequence and covers this correctly. The bootstrap doc is a higher-level summary that explicitly defers to the knowledge doc. Severity: **not blocking** on that basis.

   Re-examining strictly: the only place a gap remains is `DESIGN-index.md` lines 166–167 containing stale descriptions for `knowledge_hybrid_vector_weight` and `knowledge_hybrid_text_weight`. These descriptions predate the RRF change and now misrepresent the purpose of those settings. Since `DESIGN-index.md` is the project-wide config reference, a stale entry there is a real gap even if the authoritative knowledge doc is correct.

---

## Phase 5 — Verdict

**GAPS_FOUND**

### Priority Table

| Priority | Item | Location | Gap | Severity |
|----------|------|----------|-----|----------|
| blocking | `knowledge_hybrid_vector_weight` description | `docs/DESIGN-index.md` line 166 | Describes as "Hybrid retrieval vector-score weight" — does not note the setting is now legacy/ignored after RRF migration. A reader consulting only the index config table will believe this setting affects scoring. | blocking |
| minor | `knowledge_hybrid_text_weight` description | `docs/DESIGN-index.md` line 167 | Same staleness — "Hybrid retrieval BM25-score weight" with no indication the parameter is ignored in RRF mode. | minor |

### What is clean

All core chunking-rrf deliverables have honest, complete DESIGN doc coverage:

- `_chunker.py` / `chunk_text()` — fully documented in `DESIGN-knowledge.md`
- `chunks` / `chunks_fts` / `chunks_vec` schema — fully documented in `DESIGN-knowledge.md` Section 2.2
- `index_chunks()` / `remove_chunks()` — fully documented with semantics, atomicity, and guard conditions
- FTS source routing (memory → `docs_fts`, non-memory → `chunks_fts`) — fully documented in both knowledge docs
- RRF merge (k=60) — fully documented with formula, Cormack citation, and note that weight parameters are ignored
- `knowledge_chunk_size` / `knowledge_chunk_overlap` — fully documented in `DESIGN-knowledge.md` Section 3.1, `DESIGN-index.md`, and `DESIGN-core.md` CoConfig table
- Articles tool `index_chunks` call path — documented in `DESIGN-knowledge.md`
- Google Drive tool `index_chunks` call path — documented in `DESIGN-knowledge.md`
- Library startup sync `index_chunks` — documented in `DESIGN-knowledge.md`

### Fix Required

Update `docs/DESIGN-index.md` lines 166–167:

- `knowledge_hybrid_vector_weight`: change description from "Hybrid retrieval vector-score weight" to "Retained for backward compatibility; ignored — hybrid merge uses RRF (rank-based, not score-weighted)"
- `knowledge_hybrid_text_weight`: change description from "Hybrid retrieval BM25-score weight" to "Retained for backward compatibility; ignored — hybrid merge uses RRF (rank-based, not score-weighted)"

The corrected phrasing is already used verbatim in `DESIGN-knowledge.md` Section 3.1 — copy it.

---

**Summary:** scope=chunking-rrf | verdict=GAPS_FOUND | blocking=1 | minor=1 | output=docs/AUDIT-chunking-rrf.md
