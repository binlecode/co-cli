# REVIEW: chunking-rrf — Co-System Health Check
_Date: 2026-03-10_

## What Was Reviewed
- **DESIGN docs:** `docs/DESIGN-knowledge.md`, `docs/DESIGN-flow-knowledge-lifecycle.md`, `docs/DESIGN-index.md`
- **Source modules:** `co_cli/_knowledge_index.py`, `co_cli/_chunker.py`, `co_cli/tools/articles.py`, `co_cli/tools/google_drive.py`, `co_cli/_bootstrap.py`, `co_cli/main.py`, `co_cli/config.py`, `co_cli/deps.py`
- **TODO docs:** none matched scope (feature fully shipped)
- **DELIVERY docs:** `docs/DELIVERY-chunking-rrf.md`

## Auditor — TODO Health

No TODO docs matched scope — feature fully shipped.

## Auditor — Delivery Artifact Lifecycle

| DELIVERY doc | Verdict | Key finding |
|--------------|---------|-------------|
| DELIVERY-chunking-rrf.md | active | Gate 2 approved; Gate 3 (PO acceptance) still pending — doc must be retained until PO signs off |

### Spot-check Results

- `co_cli/_knowledge_index.py`: `CREATE TABLE IF NOT EXISTS chunks` (line 104), `CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts` (line 115), `def index_chunks` (line 412), and `def remove_chunks` (line 475) all present — matches TASK-2 claims exactly.
- `co_cli/_chunker.py`: `class Chunk` (line 5) and `def chunk_text` (line 13) both present — matches TASK-1 claims exactly.
- `tests/test_knowledge_index.py`: `test_chunks_fts_multi_document_crowding` exists at line 686 — confirms Gate 2 regression test is in place (scenario 16 crowding fix).
- Active `docs/TODO-runtime-check-and-doctor-workflow.md` contains zero references to chunking-rrf features — no downstream TODO depends on this delivery.

**Overall delivery lifecycle: active**

---

## Code Dev — Doc Accuracy Audit

| Doc | Section | Status | Finding |
|-----|---------|--------|---------|
| DESIGN-knowledge.md | 2.2 — `_fetch_reranker_texts()` description | blocking | Doc says "fetches from `chunk_id=0` (docs table) to ensure deterministic reranker input." Code actually fetches `chunks.content` for chunk-level results (non-memory) and only uses `docs.content[:200]` at `chunk_id=0` for doc-level (memory) results. The description misleads a developer into thinking only the docs table is used. |
| DESIGN-knowledge.md | 2.2 — chunks_fts crowding fix | minor | Doc says `search()` deduplicates by path (correct), but does not mention the `chunks_fetch_limit = limit * 20` crowding fix or the conditional `fetch_limit = limit * 20 if tags else limit` for the memory leg. The crowding fix is the core correctness change of this delivery and is undocumented in the DESIGN doc. |
| DESIGN-knowledge.md | 2.2 — KnowledgeIndex default `embedding_dims` | minor | Doc says "Default embedding dims for TEI is 1024 (changed from the legacy 256-dim Ollama embeddinggemma default)" under section 2.2 — this is accurate for `config.py` (`DEFAULT_KNOWLEDGE_EMBEDDING_DIMS = 1024`). However, `KnowledgeIndex.__init__` still has `embedding_dims: int = 256` as the Python-level parameter default (line 231). At runtime this is overridden by `settings.knowledge_embedding_dims` in `main.py`, so the effective default is 1024. The inconsistency between the class-level default and the settings default could mislead a developer constructing `KnowledgeIndex` directly. |
| DESIGN-index.md | §2 Config Reference — `knowledge_embedding_dims` | blocking | Config table entry says default is `256`. Actual `DEFAULT_KNOWLEDGE_EMBEDDING_DIMS` in `config.py` is `1024`. Wrong default in the reference table. |
| DESIGN-index.md | §2 Config Reference — `knowledge_reranker_provider` | minor | Config table lists valid values as `none`, `local`, `ollama`, `gemini`. Code `Literal` in `config.py` and `KnowledgeIndex` also supports `"tei"`. The `tei` option is missing from the DESIGN-index.md config table (it is correctly documented in DESIGN-knowledge.md §3.1). |
| DESIGN-knowledge.md | §3.1 Config table | pass | `knowledge_chunk_size` env var `CO_CLI_KNOWLEDGE_CHUNK_SIZE` and `knowledge_chunk_overlap` env var `CO_CLI_KNOWLEDGE_CHUNK_OVERLAP` — both match `config.py` exactly. |
| DESIGN-knowledge.md | §2.2 — dedup behavior | pass | "search() deduplicates results by path, keeping the highest-scoring chunk per document" matches code at `_fts_search` lines 660–666. Accurate. |
| DESIGN-knowledge.md | §2.2 — schema (docs table) | pass | `docs` table columns, UNIQUE constraint `(source, path, chunk_id)`, `chunks` table, `chunks_fts` virtual table, FTS triggers, `embedding_cache` — all match `_SCHEMA_SQL` exactly. |
| DESIGN-knowledge.md | §2.2 — RRF scoring | pass | `1/(k + rank + 1)` description matches `_hybrid_merge` implementation (line 1044, 1047, 1064, 1068). |
| DESIGN-knowledge.md | §2.2 — source routing | pass | Memory → `docs_fts`, non-memory → `chunks_fts`, mixed → both legs. Matches `_fts_search` and `_uses_memory_leg`/`_uses_chunks_leg` helpers. |
| DESIGN-flow-knowledge-lifecycle.md | Part 3 — article save flow | pass | Describes `index_chunks("library", path, chunk_text(...))` after FTS reindex on both new save and consolidation. Matches `save_article` code. |
| DESIGN-flow-knowledge-lifecycle.md | Part 7 — Drive indexing | pass | `index_chunks("drive", file_id, chunk_text(...))` after `knowledge_index.index(...)`. Matches `read_drive_file` tool. |
| DESIGN-flow-knowledge-lifecycle.md | Owning Code table | pass | All file paths in the table exist on disk and match actual module roles. |
| tests/test_knowledge_index.py | Crowding regression test | pass | `test_chunks_fts_multi_document_crowding` exists (line 686) and exercises the `chunks_fetch_limit = limit * 20` fix. Not mentioned in DESIGN docs (see minor finding above), but the test itself is present. |

### Finding Details

**F1 — blocking: DESIGN-knowledge.md §2.2 `_fetch_reranker_texts()` description**

The doc states: `_fetch_reranker_texts()` fetches from `chunk_id=0` (docs table) to ensure deterministic reranker input.

Actual code (`_knowledge_index.py` lines 1194–1228):
- `doc_level` candidates (chunk_index is None, i.e. memory results): fetches `docs.content[:200]` with `chunk_id=0` filter — this part of the description is correct.
- `chunk_level` candidates (chunk_index is not None, i.e. library/obsidian/drive): fetches `chunks.content` for the specific `(source, doc_path, chunk_index)` — this is NOT from the docs table.

The description implies all reranker input comes from `docs` at `chunk_id=0`, which is wrong for the non-memory leg. A developer following the doc would not understand that chunk-level candidates supply actual chunk text to the reranker, not the document preamble.

**F2 — minor: DESIGN-knowledge.md §2.2 — crowding fix undocumented**

`_fts_search` (lines 601–605) always sets `chunks_fetch_limit = limit * 20` regardless of tags, and conditionally sets `fetch_limit = limit * 20 if tags else limit` for the memory leg. The `chunks_fetch_limit = limit * 20` constant is the crowding fix: one long article with many chunk rows would otherwise crowd out other documents before Python-side dedup runs. This fix is the primary correctness contribution of the chunking-rrf delivery (confirmed by the `test_chunks_fts_multi_document_crowding` test), yet it has no documentation in DESIGN-knowledge.md §2.2 or anywhere in the DESIGN docs.

**F3 — minor: `KnowledgeIndex.__init__` `embedding_dims` default is 256 but config default is 1024**

`KnowledgeIndex.__init__` signature at line 231: `embedding_dims: int = 256`. Config default `DEFAULT_KNOWLEDGE_EMBEDDING_DIMS = 1024` (config.py line 156). At runtime, `main.py` line 108 passes `settings.knowledge_embedding_dims` so the effective default is 1024. But a developer constructing `KnowledgeIndex` directly (e.g. in tests, scripts, or tools) without explicitly setting `embedding_dims` will get 256-dim vec tables, which are incompatible with a `search.db` that was created with 1024-dim tables. DESIGN-knowledge.md §2.2 mentions "Default embedding dims for TEI is 1024 (changed from the legacy 256-dim Ollama embeddinggemma default)" but does not flag the class-level default discrepancy.

**F4 — blocking: DESIGN-index.md §2 Config Reference `knowledge_embedding_dims` default**

Config reference table (line 165) says default `256`. Source truth: `DEFAULT_KNOWLEDGE_EMBEDDING_DIMS = 1024` in `config.py`. This is the canonical config reference table — a developer setting up or tuning the system would use the wrong value.

**F5 — minor: DESIGN-index.md §2 Config Reference `knowledge_reranker_provider` missing `tei`**

Config reference table (line 168) lists valid values as `none`, `local`, `ollama`, `gemini`. The `tei` provider is fully implemented and documented in DESIGN-knowledge.md §2.2 and §3.1, but is absent from DESIGN-index.md's config table.

**Overall: 2 blocking, 3 minor**

---

## Verdict

**Overall: ACTION_REQUIRED → resolved**

| Priority | Action | Source | Status |
|----------|--------|--------|--------|
| P1 | Fix `_fetch_reranker_texts()` description in DESIGN-knowledge.md §2.2 | F1 | ✓ fixed |
| P1 | Fix `knowledge_embedding_dims` default in DESIGN-index.md: `256` → `1024` | F4 | ✓ fixed |
| P2 | Document `chunks_fetch_limit = limit * 20` crowding fix in DESIGN-knowledge.md §2.2 | F2 | deferred |
| P2 | Note `KnowledgeIndex.__init__` `embedding_dims` default discrepancy in DESIGN-knowledge.md §2.2 | F3 | deferred |
| P2 | Add `tei` to `knowledge_reranker_provider` valid values in DESIGN-index.md | F5 | deferred |

Both blocking inaccuracies fixed. Minor items (F2, F3, F5) deferred — not blocking Gate 3.

**Recommended next step:** Submit DELIVERY-chunking-rrf.md to PO for Gate 3 acceptance.
