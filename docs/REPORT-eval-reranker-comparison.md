# Reranker Comparison Benchmark

**Date:** 2026-03-30
**Eval:** `evals/eval_reranker_comparison.py`
**Corpus:** 30 synthetic docs — 5 topics × 6 docs (2 rel_para, 2 trap, 2 noise)
**Runs per config:** 3 (warm_ms = median of runs 2+)

**API Parameters Forced:**
- `knowledge_cross_encoder_reranker_url=None` — overrides the default `"http://127.0.0.1:8282"` that otherwise silently activates TEI regardless of which reranker is intended (bug fixed 2026-03-30)
- `ollama-model=qwen2.5:3b` (default)
- `runs=3` (default)

---

## Results Summary

| Reranker | NDCG@5 | MRR | Prec@3 | Rec@5 | Cold (ms) | Warm (ms) | Notes |
|----------|--------|-----|--------|-------|-----------|-----------|-------|
| FTS5 baseline | 1.00 | 1.00 | 0.67 | 1.00 | 0.8 | 0.7 | |
| LLM listwise | 1.00 | 1.00 | 0.67 | 1.00 | 6.7 | 4.7 | qwen2.5:3b via Ollama — **fell back to unranked** |

---

## Detailed Findings

### FTS5 Baseline

Pure BM25 with no reranker. NDCG@5=1.00 and Rec@5=1.00 confirm both relevant docs per topic land in the top-5 on this corpus. Prec@3=0.67 is expected: with 2 relevant docs per topic, positions 1–2 are `rel_para` docs and position 3 is typically a `trap` doc (high BM25 due to keyword repetition), giving exactly 2/3 precision.

The synthetic corpus is challenging by design — trap docs have query keywords repeated 3–6× plus in the title, giving them the highest BM25 scores. A working reranker should demote them below the paraphrase-rich `rel_para` docs.

### LLM Listwise (qwen2.5:3b via Ollama)

Ollama was reachable at `http://localhost:11434` (HTTP 200) but `/api/generate` returned 404 for `qwen2.5:3b` — the model is not pulled. The reranker caught the error, logged `"Reranking failed (ollama), using unranked"`, and returned the raw FTS5 result. All 15 calls (5 topics × 3 runs) fell back, so the LLM listwise metrics are identical to the FTS5 baseline — this is not a measurement of reranker quality.

**To obtain a real LLM listwise result:** run `ollama pull qwen2.5:3b` then re-run the eval.

### TEI Cross-Encoder

Not tested in this run — TEI was not running. TEI is the higher-priority branch in `KnowledgeIndex` (checked before LLM listwise). To test it, start a TEI instance at `http://127.0.0.1:8282` and remove the `knowledge_cross_encoder_reranker_url=None` override from `build_index`, or pass the URL explicitly.

---

## Bug Fix Context

Prior to 2026-03-30, `build_index` in the eval did not set `knowledge_cross_encoder_reranker_url`. `CoConfig` defaults this field to `"http://127.0.0.1:8282"`, so `KnowledgeIndex` always entered the `tei` branch — both the "FTS5 baseline" and "LLM listwise" configs silently ran TEI if the service happened to be up. The eval was not measuring what it claimed. The fix explicitly passes `knowledge_cross_encoder_reranker_url=None` for all `build_index` calls so each config is isolated to its intended reranker only.
