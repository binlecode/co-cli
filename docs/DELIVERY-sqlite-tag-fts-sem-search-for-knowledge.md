# Delivery: Phase 3 — LLM Listwise Reranking + Local Cross-Encoder
Date: 2026-03-01

## Task Results

| Task | done_when | Status | Notes |
|------|-----------|--------|-------|
| TASK-1: Config additions | `knowledge_reranker_provider` + `knowledge_reranker_model` fields + env vars in `config.py` | ✓ pass | Default is `"local"` (cross-encoder preferred per user intent); includes `"local"` variant beyond plan spec |
| TASK-2: KnowledgeIndex changes | Constructor params + `_rerank_results()` + `_fetch_reranker_texts()` + `_generate_rerank_scores()` + `_llm_rerank()` + `_call_reranker_llm()` + `_ollama_generate_ranked()` + `_gemini_generate_ranked()` present; FTS5+hybrid paths route through reranker | ✓ pass | Bonus: `_parse_ranked_indices()` (robust JSON unwrapping), `_local_cross_encoder_rerank()` (Phase 3.2 fastembed) |
| TASK-3: Wire in main.py | `reranker_provider` + `reranker_model` params in `KnowledgeIndex` constructor call | ✓ pass | |
| TASK-4: Tests | 3 required Phase 3 tests passing | ✓ pass | 2 additional tests: `test_local_cross_encoder_skips_gracefully_on_fastembed_import_error`, `test_local_cross_encoder_reranks_correctly` |

## Files Changed
- `co_cli/config.py` — `knowledge_reranker_provider` (Literal, default `"local"`), `knowledge_reranker_model` (str, default `""`), env var mappings
- `co_cli/knowledge_index.py` — Constructor params; `_rerank_results()`, `_fetch_reranker_texts()`, `_generate_rerank_scores()`, `_llm_rerank()`, `_call_reranker_llm()`, `_parse_ranked_indices()`, `_ollama_generate_ranked()`, `_gemini_generate_ranked()`, `_local_cross_encoder_rerank()`; FTS5 path routes through reranker in `search()`
- `co_cli/main.py` — `reranker_provider` and `reranker_model` passed to `KnowledgeIndex`
- `tests/test_knowledge_index.py` — 5 Phase 3 tests (3 required + 2 bonus)
- `docs/DESIGN-knowledge.md` — Phase 3 marked shipped; Config table updated with reranker settings; Evolution table corrected (fastembed, not llama-cpp-python); stale "not yet started" note removed
- `docs/TODO-sqlite-tag-fts-sem-search-for-knowledge.md` — Shipped Phase 3 content removed per lifecycle rule; only deferred items remain (score blending, MMR diversity)
- `evals/eval_reranker_comparison.py` — CORPUS comment fixed (stale "NO query keywords" → accurate "each keyword present 1×")

## Tests
- Files run: `tests/test_knowledge_index.py`
- Result: **pass** (31 passed, 0 failed)

## Doc Sync
- Docs checked: `docs/DESIGN-knowledge.md`
- Result: **fixed** — Phase 3 diagram updated; Evolution table dep corrected; Config table extended with 2 reranker settings; "code gap" notes for hybrid weights removed; stale "Phase 3 not yet started" paragraph replaced with accurate Phase 3 description

## Overall: DELIVERED
All 4 tasks verified passing, 31/31 tests pass, doc sync complete. Phase 3 ships ahead of plan: fastembed local cross-encoder (`"local"` provider) delivered alongside LLM listwise, defaulting to the higher-quality option.
