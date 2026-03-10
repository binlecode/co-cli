# Delivery: Article Chunking + RRF Hybrid Merge
Date: 2026-03-10

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1 | `co_cli/_chunker.py` exists with `Chunk` + `chunk_text()`; all 7 test scenarios pass | ✓ pass | |
| TASK-2 | `chunks`/`chunks_fts` tables present; `index_chunks`/`remove_chunks` present; no `chunk_id > 0` writes; `rebuild()` cleans chunk rows; scenarios 8–15 pass | ✓ pass | |
| TASK-3 | `index_chunks` called in `sync_dir` for non-memory sources; scenarios 12a, 12b, 13 pass | ✓ pass | |
| TASK-4 | `index_chunks` called at 2 sites in `articles.py`; called in `google_drive.py`; scenario 20 passes | ✓ pass | |
| TASK-5 | `knowledge_chunk_size`/`knowledge_chunk_overlap` in `config.py`, `deps.py`, `main.py` | ✓ pass | Pre-shipped; `CO_CLI_` env prefix (not `CO_` as originally spec'd) |
| TASK-6 | RRF implementation present in `_hybrid_merge()`; verified via integration paths (scenarios 10, 15, 20) | ✓ pass | TASK-6 `done_when` referenced private-method tests 16–19; those were removed as CLAUDE.md violations (no private-method tests). RRF is exercised end-to-end by the FTS routing and global union tests. |

## Files Changed
- `co_cli/_chunker.py` — new module: `Chunk` dataclass and `chunk_text()` with paragraph→line→char split priority and overlap
- `co_cli/_knowledge_index.py` — schema (`chunks`/`chunks_fts`/`chunks_vec`); `index_chunks()`; `remove_chunks()`; FTS/vec source routing (memory→docs_fts, non-memory→chunks_fts/chunks_vec); RRF merge in `_hybrid_merge()`; disabled character-level chunking in `index()`; `rebuild()`/`remove()`/`remove_stale()` cascade chunk cleanup; `sync_dir` emits chunks for non-memory sources
- `co_cli/tools/articles.py` — two `index_chunks()` call sites (new-article + consolidation paths); removed stale FTS integration-point comments
- `co_cli/tools/google_drive.py` — `index_chunks()` call after `index()` in `read_drive_file`
- `tests/test_chunker.py` — new: 7 functional tests for `chunk_text()`
- `tests/test_knowledge_index.py` — removed stale `test_chunking`; updated `test_search_filters_by_source` for new routing; added scenarios 8–15 covering chunk schema, `index_chunks`, FTS routing, remove cascade, `sync_dir`, memory recall, memory guard, global union search
- `tests/test_save_article.py` — scenario 20: long article second-half phrase is retrievable via chunks FTS
- `docs/TODO-chunking-rrf.md` — deleted (all tasks shipped)

## Tests
- Scope: full suite (all tasks delivered)
- Result: pass (61 passed — `tests/test_chunker.py`, `tests/test_knowledge_index.py`, `tests/test_save_article.py`)

## Independent Review
- Result: 1 minor finding fixed (dead code in `_chunker.py` `flush_acc()` — two unreachable lines removed); 1 minor finding fixed (hot-loop import hoisted out of per-file loop in `sync_dir`)
- Overall: clean after fixes

## Doc Sync
- Result: fixed — `DESIGN-knowledge.md` (schema, chunking description, FTS routing, RRF, config table, `_chunker.py` entry); `DESIGN-flow-knowledge-lifecycle.md` (flowchart, pseudocode, config section); `DESIGN-index.md` (module index, config table); `DESIGN-core.md` (knowledge system path references); `co_cli/tools/articles.py` and `co_cli/_knowledge_index.py` source docstrings (stale phase framing removed)

## Coverage Audit
- Result: GAPS_FOUND — 1 blocking (stale `knowledge_hybrid_vector_weight`/`text_weight` descriptions in `DESIGN-index.md` config table); 1 minor
- Disposition: sync-doc had already fixed the `DESIGN-index.md` entries by the time the delivery-audit ran; `grep` confirmed the stale text was absent. Finding was auto-resolved. See `docs/REVIEW-delivery-chunking-rrf.md` for full audit report.

## Artifact Lifecycle
- TODO status: deleted — all tasks shipped, no deferred work remains
- DELIVERY status: keep for Gate 2 and Gate 3 only

## Gate 3 Cleanup
- After PO acceptance, delete `docs/DELIVERY-chunking-rrf.md` in the same session.

## Overall: DELIVERED
All 6 tasks shipped. 61 tests pass. Docs in sync. Paragraph-aware chunking into `chunks`/`chunks_fts`/`chunks_vec`, FTS/vec source routing, and RRF hybrid merge are live.
